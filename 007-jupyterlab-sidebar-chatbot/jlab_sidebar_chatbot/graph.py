"""
챗봇 두뇌 = langgraph 그래프 (deepagents 로 생성) + InMemorySaver 체크포인터.

⚠️ 온라인/개발 전용 (모델은 OpenAI 호환 엔드포인트로 통신)
   - 실 OpenAI 또는 사내 vLLM/Ollama 등 **OpenAI 호환 REST**(/v1) 에 붙습니다.
     사내 vLLM 으로 가면 내부 네트워크라 외부망 호출은 아니지만, langchain-openai/openai
     (httpx 기반)는 폐쇄망 패키지 정책 확인이 필요합니다.
   - 환경변수만 바꾸면 dev ↔ 운영 그대로:
       OPENAI_API_KEY   : 필수
       OPENAI_BASE_URL  : 선택. 비우면 실 OpenAI, 사내 vLLM 이면 https://<host>/v1
       OPENAI_MODEL     : 선택. 기본 gpt-4o-mini, vLLM 이면 served-model-name.
   - 키는 코드/노트북에 하드코딩하지 말고 셸/jupyter env 로 주입하세요.
"""

from __future__ import annotations

import json
import os
from typing import Iterator, Optional

# 모델·엔드포인트는 환경변수로 — 사내 vLLM 운영 전환은 OPENAI_BASE_URL/MODEL 만 바꾸면 끝.
DEFAULT_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
DEFAULT_SYSTEM_PROMPT = (
    "너는 폐쇄망 Jupyter 환경에서 동작하는 친절한 도우미야. 한국어로 간결하게 답해."
)


def build_chat_graph(
    model: Optional[str] = None,
    system_prompt: Optional[str] = None,
    tools=None,
    checkpointer=None,
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
):
    """deepagents 로 langgraph 챗 그래프를 만들어 CompiledStateGraph 를 반환합니다.

    모델은 OpenAI 호환 엔드포인트(실 OpenAI / 사내 vLLM / 로컬 Ollama 등)에 붙습니다.

    파라미터 우선순위 (인자가 있으면 인자, 없으면 env):
        api_key   : 인자 > OPENAI_API_KEY  env  (없으면 RuntimeError)
        base_url  : 인자 > OPENAI_BASE_URL env  (없으면 OpenAI 기본 엔드포인트)
        model     : 인자 > OPENAI_MODEL    env  (없으면 "gpt-4o-mini")

    사내 vLLM 예시:
        build_chat_graph(api_key="...", base_url="https://vllm.사내/v1", model="<served-name>")

    멀티턴은 checkpointer(기본 InMemorySaver) + 호출 시 thread_id 로 관리됩니다.
    """
    api_key = api_key or os.environ.get("OPENAI_API_KEY")
    base_url = base_url or os.environ.get("OPENAI_BASE_URL") or None
    model = model or os.environ.get("OPENAI_MODEL") or "gpt-4o-mini"
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY 가 필요합니다. 인자(api_key=...) 또는 환경변수(OPENAI_API_KEY) 로 "
            "주입하세요. 코드/노트북에 키를 하드코딩하지 마세요."
        )
    from deepagents import create_deep_agent
    from langchain_openai import ChatOpenAI
    from langgraph.checkpoint.memory import InMemorySaver

    return create_deep_agent(
        model=ChatOpenAI(
            model=model,
            base_url=base_url,
            api_key=api_key,
            temperature=0,
        ),
        system_prompt=system_prompt or DEFAULT_SYSTEM_PROMPT,
        tools=list(tools) if tools else None,
        checkpointer=checkpointer or InMemorySaver(),
    )


def _text(content) -> str:
    """메시지 content(문자열 또는 content-block 리스트)에서 텍스트만 뽑습니다."""
    if isinstance(content, list):
        return "".join(
            b.get("text", "") if isinstance(b, dict) else str(b) for b in content
        )
    return content if isinstance(content, str) else str(content)


def _split_turn(msgs) -> dict:
    """이번 턴의 메시지 목록에서 '최종 답변 + 중간 도구 단계' 를 분리합니다.

    deepagents 는 한 턴에 여러 메시지(도구 호출 AI 메시지 · 도구 결과 등)를 만듭니다.
    프론트가 최종 답변은 그대로, 도구 관련 단계는 접어서 보여줄 수 있게 구조화합니다.

    반환: {"answer": str, "steps": [{"type", "label", "detail"}, ...]}
    """
    # 이번 턴에 새로 생긴 메시지 = 마지막 human(=방금 보낸 입력) 이후
    human_idxs = [i for i, m in enumerate(msgs) if getattr(m, "type", None) == "human"]
    turn = msgs[(human_idxs[-1] + 1):] if human_idxs else msgs

    answer = _text(turn[-1].content) if turn else ""
    steps = []
    for m in turn[:-1]:  # 마지막(=최종 답변) 제외한 중간 메시지들 = 단계
        mtype = getattr(m, "type", "?")
        text = _text(getattr(m, "content", ""))
        if mtype == "ai":
            for tc in (getattr(m, "tool_calls", None) or []):
                args = tc.get("args", {})
                steps.append({
                    "type": "tool_call",
                    "label": f"🔧 도구 호출: {tc.get('name', '?')}",
                    "detail": json.dumps(args, ensure_ascii=False, indent=2),
                })
            if text.strip():
                steps.append({"type": "thought", "label": "💭 중간 생각", "detail": text})
        elif mtype == "tool":
            steps.append({
                "type": "tool_result",
                "label": f"📄 도구 결과: {getattr(m, 'name', '?')}",
                "detail": text,
            })
        elif text.strip():
            steps.append({"type": mtype, "label": mtype, "detail": text})
    return {"answer": answer, "steps": steps}


def run_turn(graph, thread_id: str, message: str) -> dict:
    """그래프를 thread_id 로 한 턴 실행하고 '최종 답변 + 중간 도구 단계' 를 분리해 반환합니다.

    반환: {"answer": str, "steps": [{"type", "label", "detail"}, ...]}
    """
    result = graph.invoke(
        {"messages": [{"role": "user", "content": message}]},
        {"configurable": {"thread_id": thread_id}},
    )
    return _split_turn(result["messages"])


def stream_turn(graph, thread_id: str, message: str) -> Iterator[dict]:
    """그래프를 thread_id 로 한 턴 실행하며 '토큰' 을 흘려보내는 제너레이터.

    SSE 전송(server.py)이 그대로 클라이언트로 흘려보낼 수 있게 dict 이벤트를 순서대로 yield 합니다.
        {"type": "token", "text": "..."}  최종 답변이 LLM 에서 생성되는 토큰 조각 (여러 번)
        {"type": "done",  "answer": str, "steps": [...]}  끝에 한 번 — 권위 있는 최종 결과

    동작:
      - langgraph 의 stream_mode=["messages", "values"] 를 함께 켜서
          · "messages": LLM 이 뱉는 토큰 조각(AIMessageChunk) → 화면에 실시간 표시용
          · "values"  : 매 스텝의 전체 상태 → 마지막 것에서 권위 있는 answer/steps 계산
      - 도구 호출 조각은 본문(content)이 비어 있어 자연히 토큰으로 안 나갑니다.
      - 토큰은 '미리보기' 일 뿐, 최종 표시는 "done" 의 answer/steps 가 기준입니다
        (그래서 중간 생각이 잠깐 새어 보여도 done 시점에 올바르게 정리됩니다).
    """
    final_msgs = None
    for mode, chunk in graph.stream(
        {"messages": [{"role": "user", "content": message}]},
        {"configurable": {"thread_id": thread_id}},
        stream_mode=["messages", "values"],
    ):
        if mode == "messages":
            msg_chunk, _meta = chunk  # (메시지 조각, 메타데이터)
            # 토큰 스트리밍 대상은 AI 메시지 조각만 (도구 결과/사용자 메시지 제외)
            if getattr(msg_chunk, "type", "") in ("ai", "AIMessageChunk"):
                text = _text(getattr(msg_chunk, "content", ""))
                if text:
                    yield {"type": "token", "text": text}
        elif mode == "values":
            # 매번 전체 상태 — 마지막 것이 이번 턴 전체 메시지를 담습니다.
            final_msgs = chunk.get("messages") if isinstance(chunk, dict) else None

    out = _split_turn(final_msgs or [])
    yield {"type": "done", **out}


def reply(graph, thread_id: str, message: str) -> str:
    """한 턴 실행 후 최종 답변 텍스트만 반환합니다(단계는 무시)."""
    return run_turn(graph, thread_id, message)["answer"]


# ===== Example Usage =====
if __name__ == "__main__":
    # OPENAI_API_KEY 가 env 에 있어야 동작 (vLLM 이면 OPENAI_BASE_URL 도).
    g = build_chat_graph()
    tid = "demo-thread"
    for line in ["내 이름은 윤구야.", "내 이름이 뭐였지?"]:
        print(f"🙂 {line}")
        # 토큰 스트리밍 — 답변이 생성되는 대로 한 조각씩 출력
        print("🤖 ", end="", flush=True)
        steps = []
        for ev in stream_turn(g, tid, line):
            if ev["type"] == "token":
                print(ev["text"], end="", flush=True)
            elif ev["type"] == "done":
                steps = ev["steps"]
        print(f"\n   (단계 {len(steps)}개)\n" if steps else "\n")
