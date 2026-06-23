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
