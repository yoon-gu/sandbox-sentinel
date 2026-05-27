"""
챗봇 두뇌 = langgraph 그래프 (deepagents 로 생성) + InMemorySaver 체크포인터.

커스텀 LLM 추상화(Adapter/Mock/Brain) 없이, deepagents 가 만드는 langgraph
CompiledStateGraph 하나가 두뇌입니다. 멀티턴은 langgraph 체크포인터(thread_id)가
관리하므로, 매 턴 '새 user 메시지 하나'만 그래프에 전달하면 됩니다.

⚠️ 온라인/개발 전용
   - Claude API(api.anthropic.com) 호출 + anthropic/langchain-anthropic(httpx 기반)
     → 폐쇄망 네트워크/패키지 정책과 충돌. 로컬 개발/데모에서만 사용하세요.
   - API 키는 환경변수 ANTHROPIC_API_KEY 로만 읽습니다(코드/노트북에 하드코딩 금지).
"""

from __future__ import annotations

import os
from typing import Optional

# 키가 접근 가능한 현행 Claude 모델. 필요시 ANTHROPIC_MODEL 로 덮어쓰기.
DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
DEFAULT_SYSTEM_PROMPT = (
    "너는 폐쇄망 Jupyter 환경에서 동작하는 친절한 도우미야. 한국어로 간결하게 답해."
)


def build_chat_graph(
    model: str = DEFAULT_MODEL,
    system_prompt: Optional[str] = None,
    tools=None,
    checkpointer=None,
):
    """deepagents 로 langgraph 챗 그래프를 만들어 CompiledStateGraph 를 반환합니다.

    멀티턴은 checkpointer(기본 InMemorySaver)와 호출 시 thread_id 로 관리됩니다.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY 환경변수가 필요합니다. 코드/노트북에 키를 직접 넣지 말고 "
            "셸 또는 jupyter 서버 env 로 주입하세요(커널이 상속받습니다)."
        )
    from deepagents import create_deep_agent
    from langchain_anthropic import ChatAnthropic
    from langgraph.checkpoint.memory import InMemorySaver

    return create_deep_agent(
        model=ChatAnthropic(model=model, temperature=0),
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


def run_turn(graph, thread_id: str, message: str) -> dict:
    """그래프를 thread_id 로 한 턴 실행하고 '최종 답변 + 중간 도구 단계' 를 분리해 반환합니다.

    deepagents 는 한 턴에 여러 메시지(도구 호출 AI 메시지 · 도구 결과 등)를 만듭니다.
    프론트가 최종 답변은 그대로, 도구 관련 단계는 접어서 보여줄 수 있게 구조화합니다.

    반환: {"answer": str, "steps": [{"type", "label", "detail"}, ...]}
    """
    import json

    result = graph.invoke(
        {"messages": [{"role": "user", "content": message}]},
        {"configurable": {"thread_id": thread_id}},
    )
    msgs = result["messages"]
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


def reply(graph, thread_id: str, message: str) -> str:
    """한 턴 실행 후 최종 답변 텍스트만 반환합니다(단계는 무시)."""
    return run_turn(graph, thread_id, message)["answer"]


# ===== Example Usage =====
if __name__ == "__main__":
    # ANTHROPIC_API_KEY 가 env 에 있어야 동작합니다.
    g = build_chat_graph()
    tid = "demo-thread"
    for line in ["내 이름은 윤구야.", "내 이름이 뭐였지?"]:
        print(f"🙂 {line}")
        out = run_turn(g, tid, line)
        if out["steps"]:
            print(f"   (단계 {len(out['steps'])}개)")
        print(f"🤖 {out['answer']}\n")
