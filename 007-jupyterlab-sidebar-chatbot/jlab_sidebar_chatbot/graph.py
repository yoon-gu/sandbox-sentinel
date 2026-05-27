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


def make_graph():
    """LangGraph Server(`langgraph dev`) 용 그래프 팩토리.

    langgraph.json 의 graphs 항목이 이 함수를 가리킵니다. LangGraph 플랫폼이 thread
    영속화(체크포인터)를 직접 제공하므로, 여기서는 **체크포인터 없이** 컴파일합니다.
    (멀티턴은 플랫폼의 thread 가 관리 — 프론트는 thread_id 로 run 을 호출.)
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY 환경변수가 필요합니다.")
    from deepagents import create_deep_agent
    from langchain_anthropic import ChatAnthropic

    return create_deep_agent(
        model=ChatAnthropic(model=DEFAULT_MODEL, temperature=0),
        system_prompt=DEFAULT_SYSTEM_PROMPT,
    )


def reply(graph, thread_id: str, message: str) -> str:
    """그래프를 thread_id 로 한 턴 호출하고 마지막 assistant 텍스트를 돌려줍니다.

    매 턴 '새 user 메시지'만 전달합니다 — 이전 대화는 그래프의 체크포인터가 복원합니다.
    """
    result = graph.invoke(
        {"messages": [{"role": "user", "content": message}]},
        {"configurable": {"thread_id": thread_id}},
    )
    last = result["messages"][-1]
    content = getattr(last, "content", last)
    # Claude 응답은 문자열이거나 content block 리스트일 수 있음 — 텍스트만 합쳐 반환
    if isinstance(content, list):
        content = "".join(
            b.get("text", "") if isinstance(b, dict) else str(b) for b in content
        )
    return content if isinstance(content, str) else str(content)


# ===== Example Usage =====
if __name__ == "__main__":
    # ANTHROPIC_API_KEY 가 env 에 있어야 동작합니다.
    g = build_chat_graph()
    tid = "demo-thread"
    for line in ["내 이름은 윤구야.", "내 이름이 뭐였지?"]:
        print(f"🙂 {line}")
        print(f"🤖 {reply(g, tid, line)}\n")
