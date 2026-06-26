"""
ipykernel Comm 으로 챗봇을 노트북 커널에 직접 등록하는 '서버 없는' 전송 계층.

server.py 와 같은 두뇌(graph.py 의 langgraph 그래프)를 쓰되, localhost HTTP 서버를
띄우지 않습니다. 대신 **커널 ↔ 프론트엔드(JupyterLab)** 사이에 이미 깔려 있는
Jupyter Comm 채널(같은 ZMQ 연결) 위로 메시지를 주고받습니다.

왜 Comm 인가?
  - HTTP 서버(server.py)는 '브라우저 127.0.0.1 == 커널 127.0.0.1' 전제가 필요하고,
    포트를 열며, CORS/SSE 를 직접 다뤄야 합니다. 원격 커널(JupyterHub 등)에서는
    브라우저가 커널 포트에 닿지 못해 깨질 수 있습니다.
  - Comm 은 노트북이 이미 쓰는 커널 연결을 그대로 타므로 **새 포트가 필요 없고**,
    어디서 실행되든(로컬/원격) 동작합니다. (ipywidgets 가 쓰는 바로 그 채널입니다.)

프로토콜 (프론트엔드 → 커널, comm.on_msg 로 수신하는 msg['content']['data']):
    {type: "message", message: str, session_id: str}   한 턴 대화
    {type: "reset",   session_id: str}                  세션 thread 새로 분기(기록 비움)

프로토콜 (커널 → 프론트엔드, comm.send 로 송신 — 모두 JSON 직렬화 가능):
    {type: "token", text: str}                 최종 답변 토큰 조각 (여러 번)
    {type: "done",  answer: str, steps: [...]}  끝에 한 번 — 권위 있는 최종 결과
    {type: "error", error: str}                 실패 시
    {type: "reset_ok", session_id: str}         reset 처리 완료 알림
    {type: "ready"}                             comm_open 직후 1회 — 채널 살아있음 신호

⚠️ server.py 와 동일하게 '온라인/개발 전용'(graph.py 가 실제 모델을 호출). base_url=None
   이면 graph.py 의 고정 응답(stub) 그래프가 쓰여 모델 호출 없이 점검할 수 있습니다.

사용 (노트북 셀):
    from jlab_sidebar_chatbot import register_chatbot_comm

    # 로컬 Ollama (키 불필요, 생각 끄고 가장 빠르게)
    register_chatbot_comm(provider="ollama", model="qwen3.5:0.8b")

    # 사내 vLLM (OpenAI 호환 /v1)
    register_chatbot_comm(api_key="...", base_url="https://vllm.사내/v1", model="<name>")

    # 모델 없이 프론트만 점검 (고정 응답 stub)
    register_chatbot_comm(base_url=None)
"""

from __future__ import annotations

import threading
from typing import Dict, Optional

from .graph import build_chat_graph, stream_turn

# ===== 1. 상수 =====
# ⚠️ 프론트엔드(labextension)가 comm 을 열 때 쓰는 target_name 과 반드시 일치해야 합니다.
#    server.py 의 DEFAULT_PORT 가 handler.ts 와 일치해야 하는 것과 같은 약속입니다.
COMM_TARGET = "jlab_sidebar_chatbot"

# 이 커널에 등록해 둔 target 들의 정리(unregister)용 정보:
#   target_name -> {"on_open": 콜백, "open_comms": 살아있는 comm 들}
_registered: Dict[str, dict] = {}


# ===== 2. 유틸리티 함수 =====
def _get_kernel():
    """현재 실행 중인 ipykernel 커널을 돌려줍니다. ipykernel 밖이면 RuntimeError.

    판별 방법: IPython 셸이 ipykernel 안에서 돌 때만 셸 객체에 `.kernel` 속성이 붙습니다
    (ZMQInteractiveShell). 일반 터미널 IPython/python REPL 에는 `.kernel` 이 없습니다.
    """
    try:
        from IPython import get_ipython
    except ImportError as exc:  # IPython 자체가 없는 순수 파이썬
        raise RuntimeError(
            "register_chatbot_comm 은 JupyterLab 노트북(ipykernel) 안에서만 동작합니다 "
            "— IPython 을 찾을 수 없습니다."
        ) from exc

    ip = get_ipython()
    if ip is None:
        raise RuntimeError(
            "register_chatbot_comm 은 ipykernel 안에서 실행해야 합니다 "
            "(get_ipython() 이 None — 노트북이 아니라 일반 파이썬 프로세스로 보입니다). "
            "JupyterLab 노트북 셀에서 실행하세요."
        )

    kernel = getattr(ip, "kernel", None)
    if kernel is None or getattr(kernel, "comm_manager", None) is None:
        raise RuntimeError(
            "ipykernel 커널을 찾을 수 없습니다 (get_ipython().kernel 없음/불완전). "
            "터미널 IPython 이 아니라 JupyterLab/Notebook 의 노트북 셀에서 실행하세요."
        )
    return kernel


# ===== 3. 핵심 함수 =====
def register_chatbot_comm(
    provider: Optional[str] = None,
    model: Optional[str] = None,
    system_prompt: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    **kw,
):
    """langgraph 그래프를 한 번 만들고, 이 커널에 '챗봇 Comm target' 을 등록합니다.

    프론트엔드(사이드바)가 target_name=COMM_TARGET 으로 comm 을 열면, 그때마다
    on_open 이 호출되어 메시지 핸들러를 붙입니다. HTTP 서버/포트 없이 동작합니다.

    인자는 build_chat_graph 와 동일합니다 (server.start_graph_server 와 같은 옵션):
      - provider : "openai"(기본) | "ollama"
      - model / system_prompt / api_key / base_url
      - **kw     : thinking / tools / checkpointer 등 build_chat_graph 의 나머지 인자
      - base_url=None 이면 graph.py 의 고정 응답(stub) 그래프가 쓰입니다(모델 호출 0).

    반환: 등록된 langgraph 그래프 객체(같은 옵션으로 다시 부르면 target 을 덮어씁니다).

    ipykernel 밖에서 부르면 RuntimeError 를 던집니다.
    """
    # ① ipykernel 안인지 먼저 확인 — 아니면 그래프를 만들기 전에 바로 실패시킵니다.
    kernel = _get_kernel()

    # ② 그래프는 '한 번만' 만듭니다(server.py 와 동일). 같은 base_url=None stub 분기,
    #    provider='ollama' 네이티브 분기를 build_chat_graph 가 그대로 처리합니다.
    graph = build_chat_graph(
        model=model,
        system_prompt=system_prompt,
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        **kw,
    )

    # ③ 세션별 '대화 세대' — server.py 와 동일한 reset 전략.
    #    /reset 대신 {type:'reset'} 메시지로 thread_id 를 새로 분기해 기록을 비웁니다.
    reset_gen: Dict[str, int] = {}

    def thread_id(session_id: str) -> str:
        return f"{session_id}#{reset_gen.get(session_id, 0)}"

    # 이 target 으로 열린 살아있는 comm 들(정리/디버깅용)
    open_comms: set = set()

    def on_open(comm, open_msg):
        """프론트엔드가 comm 을 열 때 호출되는 콜백 (시그니처: comm, open_msg).

        comm        : 이 연결 전용 Comm 객체. comm.send / comm.on_msg / comm.on_close 제공.
        open_msg    : comm_open 원본 메시지(dict). 여기선 쓰지 않습니다.
        """
        open_comms.add(comm)

        def handler(msg):
            """프론트엔드가 보낸 한 건의 comm_msg 를 처리합니다.

            실제 페이로드는 msg['content']['data'] 에 들어 있습니다(JSON dict).
            """
            data = (msg.get("content") or {}).get("data") or {}
            mtype = data.get("type")
            session_id = (data.get("session_id") or "default") or "default"

            # ─ 리셋: 해당 세션의 thread_id 를 새로 분기(langgraph 기록 비움) ─
            if mtype == "reset":
                reset_gen[session_id] = reset_gen.get(session_id, 0) + 1
                comm.send({"type": "reset_ok", "session_id": session_id})
                return

            # ─ 한 턴 대화 ─
            if mtype == "message":
                message = (data.get("message") or "").strip()
                if not message:
                    comm.send({"type": "error", "error": "message 가 비어 있습니다"})
                    return
                _run_turn(comm, graph, thread_id(session_id), message)
                return

            comm.send({"type": "error", "error": f"알 수 없는 type: {mtype!r}"})

        def on_close(_msg):
            open_comms.discard(comm)

        comm.on_msg(handler)
        comm.on_close(on_close)
        # 채널이 살아있다는 신호(프론트가 이걸 받으면 '연결됨' 표시 가능). 선택적이지만 무해.
        comm.send({"type": "ready"})

    # ④ target 등록. 같은 이름으로 다시 부르면 콜백이 교체됩니다(그래프 갱신 = 재등록).
    kernel.comm_manager.register_target(COMM_TARGET, on_open)
    _registered[COMM_TARGET] = {"on_open": on_open, "open_comms": open_comms}

    print(
        f"✅ 챗봇 Comm 등록됨: target='{COMM_TARGET}' (HTTP/포트 없음). "
        "우측 💬 탭에서 대화하세요."
    )
    return graph


def _run_turn(comm, graph, tid: str, message: str) -> None:
    """한 턴을 실행하며 stream_turn 의 이벤트를 그대로 comm.send 로 흘려보냅니다.

    stream_turn 이 주는 이벤트는 이미 프론트가 기대하는 모양입니다:
        {"type":"token","text":...}  /  {"type":"done","answer":...,"steps":[...]}
    그래서 변환 없이 comm.send(ev) 만 합니다(server.py 의 SSE 와 같은 페이로드).

    ⚠️ 동기 실행: 이 핸들러가 도는 동안 커널은 'busy' 가 되어(셀 실행과 같은 shell
       채널) 다른 셀 실행이 잠깐 대기합니다 — 일반 셀이 도는 것과 동일한 체감입니다.
       긴 답변 동안 커널을 계속 반응하게 하려면 daemon 스레드로 감싸고 그 안에서
       comm.send 를 호출해도 됩니다(modern ipykernel 의 IOPub 송신은 스레드 안전).
    """
    try:
        for ev in stream_turn(graph, tid, message):
            comm.send(ev)
    except Exception as exc:
        # 전송 경계의 광범위 catch — server.py 와 동일하게 그래프/LLM 의 임의 예외를
        # 사용자에게 error 이벤트로 전달합니다(커널을 죽이지 않음).
        try:
            comm.send({"type": "error", "error": f"그래프 호출 실패: {exc}"})
        except Exception:
            # comm 이 이미 닫혔으면 보낼 곳이 없습니다 — 조용히 종료.
            pass


def unregister_chatbot_comm() -> None:
    """등록한 챗봇 Comm target 을 해제하고 열린 comm 들을 닫습니다(stop_graph_server 대응)."""
    info = _registered.pop(COMM_TARGET, None)
    if info is None:
        print(f"등록된 Comm target 이 없습니다: '{COMM_TARGET}'")
        return
    try:
        kernel = _get_kernel()
        kernel.comm_manager.unregister_target(COMM_TARGET, info["on_open"])
    except RuntimeError:
        pass  # 이미 커널 밖이거나 종료 중 — 무시
    for comm in list(info["open_comms"]):
        try:
            comm.close()
        except Exception:
            pass
    print(f"해제됨: Comm target '{COMM_TARGET}'")


# ===== 4. Example Usage =====
if __name__ == "__main__":
    # 이 모듈은 ipykernel(노트북) 안에서만 동작합니다. 일반 python 으로 실행하면
    # get_ipython() 이 None 이라 register_chatbot_comm 이 RuntimeError 를 던집니다.
    print(
        "이 모듈은 JupyterLab 노트북 셀에서 사용하세요:\n"
        "    from jlab_sidebar_chatbot import register_chatbot_comm\n"
        "    register_chatbot_comm(provider='ollama', model='qwen3.5:0.8b')\n"
    )
