"""
챗봇 두뇌 = langgraph 그래프 (deepagents 로 생성) + InMemorySaver 체크포인터.

⚠️ 온라인/개발 전용. 두 가지 백엔드(provider)를 지원합니다:
   - provider="openai" (기본): OpenAI 호환 /v1 — 실 OpenAI / 사내 vLLM / 로컬 Ollama(/v1).
       langchain-openai(httpx 기반)는 폐쇄망 패키지 정책 확인이 필요합니다.
       OPENAI_API_KEY   : 필수
       OPENAI_BASE_URL  : 선택. 비우면 실 OpenAI, 사내 vLLM 이면 https://<host>/v1
       OPENAI_MODEL     : 선택. 기본 gpt-4o-mini, vLLM 이면 served-model-name.
   - provider="ollama": Ollama 네이티브 /api/chat (ChatOllama). 키 불필요이고, 생각(thinking)
       끄기가 동작해 첫 토큰이 즉시 나옵니다(가장 빠름). langchain-ollama 패키지 필요.
       CHAT_PROVIDER : "openai" | "ollama" (기본 openai)
       OLLAMA_MODEL  : 기본 qwen3.5:0.8b
       OLLAMA_BASE_URL : 기본 http://localhost:11434 (루트 URL — .../v1 아님)
   - 키는 코드/노트북에 하드코딩하지 말고 셸/jupyter env 로 주입하세요.
"""

from __future__ import annotations

import json
import os
import re
import warnings
from typing import Iterator, Optional

# 모델·엔드포인트 기본값은 인자 > 환경변수 순으로 정해집니다 — 사내 vLLM 전환은
# base_url/model 만 바꾸면 끝. (openai 기본 모델 "gpt-4o-mini" 는 build_chat_graph 안에서 처리)
# Ollama(네이티브) 기본값 — provider="ollama" 일 때 사용.
DEFAULT_OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3.5:0.8b")
DEFAULT_OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
DEFAULT_SYSTEM_PROMPT = (
    "너는 폐쇄망 Jupyter 환경에서 동작하는 친절한 도우미야. 한국어로 간결하게 답해."
)

# ── base_url 이 None 일 때 돌려줄 고정 응답(stub) ─────────────────────────────
# 실제 모델(OpenAI/vLLM/Ollama) 호출 없이 이 문자열만 돌려주는 더미 모드입니다.
# 프론트엔드의 SSE 토큰 스트리밍·마크다운 렌더·코드블록 복사 버튼을 모델 없이
# 점검할 때 씁니다. (마크다운 + 파이썬 코드블록을 일부러 포함해 렌더 결과를 보여줍니다.)
FIXED_STUB_ANSWER = (
    "안녕하세요! 저는 **고정 응답 모드(stub)** 로 동작 중인 데모 챗봇입니다.\n\n"
    "`base_url=None` 으로 그래프를 만들면 실제 모델을 호출하지 않고 이 고정 문자열을 "
    "돌려줍니다 — 프론트엔드의 SSE 스트리밍·마크다운 렌더·코드블록 복사 버튼을 모델 "
    "없이 점검하기 위함입니다.\n\n"
    "```python\n"
    "def add(a, b):\n"
    '    """두 수를 더해 돌려줍니다."""\n'
    "    return a + b\n\n"
    "print(add(2, 3))  # 5\n"
    "```\n\n"
    '실제 모델로 바꾸려면 `provider="ollama"` 로 호출하거나 `base_url`/`api_key` 를 '
    "지정하세요."
)


class _StubMessage:
    """더미 그래프가 주고받는 최소 메시지 객체.

    run_turn / stream_turn / _split_turn 이 쓰는 속성(`type`, `content`, `tool_calls`,
    `name`)만 흉내 냅니다 — langchain 메시지 클래스에 의존하지 않습니다.
    """

    def __init__(self, type: str, content: str):
        self.type = type
        self.content = content
        self.tool_calls = []  # 더미는 도구를 부르지 않습니다
        self.name = None


class _StubGraph:
    """LLM 없이 고정 문자열만 돌려주는 더미 그래프 (실제 모델 호출 0).

    CompiledStateGraph 대신, run_turn(`invoke`) / stream_turn(`stream`) 이 호출하는
    딱 두 메서드만 같은 시그니처로 흉내 냅니다. 멀티턴 상태는 없습니다(매 턴 같은 응답).
    쓰임새: ① build_chat_graph(base_url=None) ② start_test_server() — 사이드바가
    서버에 '연결되는지'만 점검(전송 경로는 실제와 동일, 두뇌만 더미).

    answer 를 주면 그 문자열을, 안 주면 기본 고정 문자열(FIXED_STUB_ANSWER)을 씁니다.
    """

    def __init__(self, answer: Optional[str] = None):
        self.answer = answer if answer is not None else FIXED_STUB_ANSWER

    @staticmethod
    def _last_user(inputs) -> str:
        msgs = (inputs or {}).get("messages") or []
        return msgs[-1].get("content", "") if msgs else ""

    def invoke(self, inputs, config=None):
        # 한 턴 = [사용자 입력, 고정 답변] 두 메시지로 구성합니다.
        human = _StubMessage("human", self._last_user(inputs))
        ai = _StubMessage("ai", self.answer)
        return {"messages": [human, ai]}

    def stream(self, inputs, config=None, stream_mode=None):
        # 토큰 스트리밍 흉내: 고정 문자열을 작은 조각으로 흘립니다(messages 모드).
        human = _StubMessage("human", self._last_user(inputs))
        for piece in re.findall(r".{1,8}", self.answer, re.S):
            yield ("messages", (_StubMessage("ai", piece), {}))
        # 끝에 values 모드로 이번 턴 전체 메시지를 한 번 — stream_turn 이 여기서
        # 권위 있는 answer/steps 를 계산합니다.
        ai = _StubMessage("ai", self.answer)
        yield ("values", {"messages": [human, ai]})


def _build_chat_model(provider, model, api_key, base_url, thinking):
    """provider 에 맞는 langchain chat 모델을 만듭니다 (토큰 스트리밍 + thinking 토글).

    thinking 의미: None=모델 기본(스위치 안 보냄) / False=생각 끔 / True=생각 유지.

    - "openai": ChatOpenAI (실 OpenAI / 사내 vLLM 등 OpenAI 호환 /v1 엔드포인트).
        · streaming=True 로 토큰을 조각조각 흘립니다(없으면 통으로 나옴).
        · thinking 이 '명시적으로 False' 이고 자체 서빙(base_url 지정)이며 실 OpenAI 가
          아닐 때만 extra_body={"chat_template_kwargs": {"enable_thinking": False}} 를
          붙여 생각을 끕니다. (thinking=None 기본이면 아무 스위치도 안 보내 기존 vLLM
          동작을 그대로 유지 — 모르는 파라미터로 400 나는 걸 방지.)
          (Ollama 의 /v1 은 이 스위치를 무시하므로 Ollama 는 아래 ChatOllama 를 쓰세요.)
    - "ollama": ChatOllama (Ollama 네이티브 /api/chat).
        · reasoning=False 가 네이티브 think:false → 생각 단계를 꺼서 첫 토큰이 즉시 나옵니다.
          (Ollama 에서 thinking 을 끄는 유일하게 동작하는 스위치) · API 키 불필요.
    """
    if provider == "ollama":
        try:
            from langchain_ollama import ChatOllama
        except ImportError as exc:  # 패키지 미설치 시 친절한 안내로 바꿔 전달
            raise RuntimeError(
                "provider='ollama' 에는 langchain-ollama 패키지가 필요합니다. "
                "설치: pip install langchain-ollama  "
                "(OpenAI 호환 엔드포인트면 provider='openai'(기본) + langchain-openai 를 쓰세요.)"
            ) from exc

        return ChatOllama(
            model=model,
            base_url=base_url or DEFAULT_OLLAMA_BASE_URL,
            temperature=0,
            # reasoning: None=모델 기본 / False=생각 OFF / True=유지.
            # (thinking=True 는 reasoning 지원 모델에서만 의미 있고, 아니면 조용히 무시됩니다)
            reasoning=thinking,
        )

    # provider == "openai" (실 OpenAI / 사내 vLLM)
    from langchain_openai import ChatOpenAI

    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY 가 필요합니다. 인자(api_key=...) 또는 환경변수(OPENAI_API_KEY) 로 "
            "주입하세요. 코드/노트북에 키를 하드코딩하지 마세요. "
            "(로컬 Ollama 를 쓰려면 provider='ollama' 로 호출하세요 — 키 불필요.)"
        )
    extra_body = None
    if thinking is False and base_url and "openai.com" not in base_url:
        # '명시적으로 thinking=False' + 자체 서빙(vLLM 등) + 실 OpenAI 아님 일 때만.
        extra_body = {"chat_template_kwargs": {"enable_thinking": False}}
    return ChatOpenAI(
        model=model,
        base_url=base_url,
        api_key=api_key,
        temperature=0,
        # ⚠️ 토큰 스트리밍의 핵심. 이 값이 없으면(기본 False) ChatOpenAI 가 비스트리밍
        # 호출을 해서 langgraph stream_mode="messages" 가 토큰을 조각으로 못 받고
        # '최종 답변 한 덩어리'만 흘립니다(통으로). True 면 토큰이 '다다다닥' 흘러갑니다.
        streaming=True,
        extra_body=extra_body,
    )


def build_chat_graph(
    model: Optional[str] = None,
    system_prompt: Optional[str] = None,
    tools=None,
    checkpointer=None,
    *,
    provider: Optional[str] = None,
    thinking: Optional[bool] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
):
    """deepagents 로 langgraph 챗 그래프를 만들어 CompiledStateGraph 를 반환합니다.

    provider 로 두 가지 백엔드를 고릅니다 (생각/thinking 끄는 방식이 서로 다름):
      - "openai" (기본): ChatOpenAI — 실 OpenAI / 사내 vLLM 등 OpenAI 호환 /v1.
            thinking 을 명시적으로 False 로 주면 자체 서빙(vLLM)에서 enable_thinking=False
            로 생각을 끕니다. (기본 None 이면 아무 스위치도 안 보내 기존 동작 그대로.)
      - "ollama": ChatOllama — Ollama 네이티브 /api/chat. API 키 불필요.
            thinking 미지정이면 자동으로 끔(False) → 첫 토큰 즉시(가장 빠름).
            langchain-ollama 패키지가 필요합니다.

    파라미터 우선순위 (인자 > env > 기본값):
        provider : 인자 > CHAT_PROVIDER env > "openai"
        thinking : 인자(None=미지정). None 이면 openai=스위치 안 보냄 / ollama=끔(False).
                   False=생각 끔, True=생각 유지.
        model    : 인자 > (ollama: OLLAMA_MODEL / openai: OPENAI_MODEL) > 기본값
        base_url : 인자 > (ollama: OLLAMA_BASE_URL / openai: OPENAI_BASE_URL)
        api_key  : 인자 > OPENAI_API_KEY  (openai 한정, 없으면 RuntimeError)

    예시:
        # 사내 vLLM
        build_chat_graph(api_key="...", base_url="https://vllm.사내/v1", model="<served-name>")
        # 로컬 Ollama (생각 끄고 가장 빠르게)
        build_chat_graph(provider="ollama", model="qwen3.5:0.8b")

    멀티턴은 checkpointer(기본 InMemorySaver) + 호출 시 thread_id 로 관리됩니다.
    """
    provider = (provider or os.environ.get("CHAT_PROVIDER") or "openai").strip().lower()
    if provider not in ("openai", "ollama"):
        raise ValueError(
            f"provider 는 'openai' 또는 'ollama' 여야 합니다 (받은 값: {provider!r})"
        )

    # thinking 기본값: ollama 는 미지정(None)이면 끔(False)으로 — 첫 토큰이 즉시 나오게.
    # openai 는 None 그대로 둬서 아무 스위치도 안 보냅니다(기존 vLLM 동작을 안 깨뜨림).
    if thinking is None and provider == "ollama":
        thinking = False

    if provider == "ollama":
        if api_key:
            warnings.warn(
                "provider='ollama' 는 api_key 가 필요 없습니다 — 전달한 키는 무시됩니다.",
                UserWarning,
            )
        model = model or os.environ.get("OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL
        base_url = base_url or os.environ.get("OLLAMA_BASE_URL") or DEFAULT_OLLAMA_BASE_URL
        # ChatOllama 는 루트 URL(.../api/chat 를 알아서 붙임)을 원합니다. OpenAI 처럼 ".../v1"
        # 을 줬으면 떼어내고 알려줍니다(안 그러면 /v1/api/chat 으로 잘못 호출됨).
        if base_url.rstrip("/").endswith("/v1"):
            fixed = base_url.rstrip("/")[: -len("/v1")] or "/"
            warnings.warn(
                f"Ollama base_url 은 루트여야 합니다(.../v1 아님). '{base_url}' → '{fixed}' 로 보정합니다.",
                UserWarning,
            )
            base_url = fixed
    else:
        api_key = api_key or os.environ.get("OPENAI_API_KEY")
        base_url = base_url or os.environ.get("OPENAI_BASE_URL") or None
        model = model or os.environ.get("OPENAI_MODEL") or "gpt-4o-mini"

    # base_url 이 (인자·env 모두 비어) None 으로 확정되면 실제 모델을 만들지 않고
    # 고정 문자열만 돌려주는 더미 그래프를 반환합니다. ⚠️ 임시 stub 모드 —
    # 실제 OpenAI/vLLM/Ollama 를 쓰려면 base_url 을 명시하거나 provider="ollama" 로 호출하세요.
    # (ollama 는 base_url 기본값이 채워지므로 이 분기에 걸리지 않습니다.)
    if base_url is None:
        return _StubGraph()

    from deepagents import create_deep_agent
    from langgraph.checkpoint.memory import InMemorySaver

    chat_model = _build_chat_model(provider, model, api_key, base_url, thinking)
    return create_deep_agent(
        model=chat_model,
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
