"""
003-langgraph-chat-repl — basic usage

MockLLM + 최소 LangGraph 그래프를 REPL 로 기동하는 예제.
실행:
    cd 003-langgraph-chat-repl
    python examples/basic_usage.py
"""
from __future__ import annotations

import os
import re
import sys
import time
from typing import Annotated, Any, Optional, TypedDict

# 상위 디렉토리의 repl.py import (로컬 실행용)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from repl import launch, Tracer  # noqa: E402

from langgraph.checkpoint.memory import MemorySaver  # noqa: E402
from langgraph.graph import END, START, StateGraph  # noqa: E402
from langgraph.types import Command, interrupt  # noqa: E402


# ============================================================
# MockLLM — HITL 트리거를 포함한 에코 스타일 시뮬레이터
# ============================================================
class MockLLM:
    """외부 모델 의존 없이 workflow 만 보여주는 시뮬레이터.

    사용자 메시지의 특정 키워드를 만나면 assistant 응답에 `ask_user` 페이로드를
    실어서 보내고, 그걸 본 graph 의 human 노드가 interrupt() 를 발동해 REPL
    에서 HITL UI 가 뜨도록 유도.
    """

    def __init__(self, name: str = "mock-llm-repl",
                 per_token_ms: float = 5.0,
                 tracer: Optional[Tracer] = None) -> None:
        self.name = name
        self.per_token_ms = per_token_ms
        self.tracer = tracer

    def invoke(self, messages: list[dict]) -> dict:
        if self.tracer is None:
            reply, _, _ = self._generate(messages)
            return reply
        with self.tracer.span(
            f"LLM:{self.name}", kind="llm",
            inputs=messages, metadata={"model": self.name},
        ) as s:
            reply, ti, to = self._generate(messages)
            self.tracer.finish(s, outputs=reply, tokens_in=ti, tokens_out=to)
            return reply

    def _generate(self, messages: list[dict]) -> tuple[dict, int, int]:
        last_user = next(
            (m["content"] for m in reversed(messages) if m.get("role") == "user"),
            "",
        )
        tail = messages[-1] if messages else {}
        tool_reply = tail.get("content") if tail.get("role") == "tool" else None
        turn = sum(1 for m in messages if m.get("role") == "user")

        # 직전 assistant 가 되묻기했고, 지금 그 답변이 들어온 경우 — 최종 응답
        prev_ask = None
        for m in reversed(messages[:-1]):
            if m.get("role") == "assistant":
                prev_ask = m.get("ask_user")
                break
        if prev_ask:
            qtype = prev_ask.get("type", "input")
            tag = {"choice": "객관식", "multi_choice": "복수선택"}.get(qtype, "주관식")
            text = (
                f"[턴 {turn}] ({tag} 응답 수신) "
                f"사용자 답변 '{str(last_user)[:60]}' 을(를) 반영해 다음 단계를 진행합니다."
            )
            return self._finalize(text, messages)

        if tool_reply:
            text = (
                f"[턴 {turn}] 도구 실행 결과를 확인했습니다. → {tool_reply} "
                f"(원 질문: '{last_user[:60]}')"
            )
            return self._finalize(text, messages)

        # 복수선택 유도 키워드 (객관식보다 먼저 검사)
        if any(k in last_user for k in ("여러", "복수", "해당", "체크", "모두")):
            question = "관심 있는 항목을 모두 체크해주세요."
            options = ["주식", "채권", "부동산", "현금성 자산", "대체투자"]
            reply, ti, to = self._finalize(question, messages)
            reply["ask_user"] = {
                "type": "multi_choice",
                "question": question,
                "options": options,
            }
            return reply, ti, to

        # 객관식 유도 키워드
        if any(k in last_user for k in ("추천", "고를", "고르", "골라", "선택지", "옵션")):
            question = "아래 중 어떤 방향이 좋을까요?"
            options = [
                "안정적 (저위험/저수익)",
                "균형형 (중간)",
                "적극적 (고위험/고수익)",
            ]
            reply, ti, to = self._finalize(question, messages)
            reply["ask_user"] = {
                "type": "choice",
                "question": question,
                "options": options,
            }
            return reply, ti, to

        # 주관식 유도 키워드
        if any(k in last_user for k in ("설명해", "알려줘", "명확", "모호", "구체적")):
            question = "조금 더 구체적으로 알려주시겠어요? 한 줄로 상황을 설명해주세요."
            reply, ti, to = self._finalize(question, messages)
            reply["ask_user"] = {"type": "input", "question": question}
            return reply, ti, to

        # 평범한 응답
        text = (
            f"[턴 {turn}] 입력: '{last_user[:80]}'. "
            f"대화에는 지금까지 {len(messages)}개 메시지가 누적되어 있습니다."
        )
        return self._finalize(text, messages)

    def _finalize(self, text: str, messages: list[dict]) -> tuple[dict, int, int]:
        tokens_in = sum(len(str(m.get("content", "")).split()) for m in messages)
        tokens_out = len(text.split())
        time.sleep(tokens_out * self.per_token_ms / 1000.0)
        return {"role": "assistant", "content": text}, tokens_in, tokens_out


# ============================================================
# 최소 LangGraph — chat + human (HITL) 두 노드
# ============================================================
def _append_messages(left: Optional[list], right: Any) -> list:
    if right is None:
        return left or []
    if not isinstance(right, list):
        right = [right]
    return (left or []) + right


class ChatState(TypedDict, total=False):
    messages: Annotated[list, _append_messages]
    pending_ask: Optional[dict]


def _looks_like_calc(text: str) -> bool:
    keys = ("계산", "더하", "합계", "더해", "+", "sum")
    return any(k in text for k in keys) and bool(re.search(r"-?\d", text))


def _run_calculator(text: str) -> dict:
    nums = [int(n) for n in re.findall(r"-?\d+", text)]
    result = sum(nums) if nums else 0
    content = f"계산기 결과: {result} (입력 숫자: {nums})"
    return {"role": "tool", "name": "calculator", "content": content}


def _chat_node(state: ChatState, config) -> dict:
    cfg = (config or {}).get("configurable", {})
    tracer: Optional[Tracer] = cfg.get("tracer")
    llm = cfg.get("llm")
    if llm is None:
        raise RuntimeError("config['configurable']['llm'] 에 LLM 어댑터를 주입하세요.")

    last_user = state["messages"][-1]["content"]
    new_messages: list[dict] = []

    if _looks_like_calc(last_user):
        if tracer is not None:
            with tracer.span("tool:calculator", kind="tool",
                             inputs={"query": last_user}) as s:
                tool_msg = _run_calculator(last_user)
                tracer.finish(s, outputs=tool_msg)
        else:
            tool_msg = _run_calculator(last_user)
        new_messages.append(tool_msg)
        assistant = llm.invoke(state["messages"] + [tool_msg])
    else:
        assistant = llm.invoke(state["messages"])

    new_messages.append(assistant)

    pending_ask = None
    if isinstance(assistant, dict):
        candidate = assistant.get("ask_user")
        if isinstance(candidate, dict) and candidate.get("question"):
            pending_ask = candidate

    return {"messages": new_messages, "pending_ask": pending_ask}


def _human_node(state: ChatState, config) -> dict:
    """HITL — 사용자 응답 받을 때까지 그래프 일시정지."""
    ask = state.get("pending_ask") or {}
    answer = interrupt(ask)  # 반드시 첫 문장

    cfg = (config or {}).get("configurable", {})
    tracer: Optional[Tracer] = cfg.get("tracer")
    if tracer is not None:
        with tracer.span(
            "human:answered",
            kind="tool",
            inputs={"ask": ask},
            metadata={"type": ask.get("type")},
        ) as s:
            tracer.finish(s, outputs={"answer": answer})

    if isinstance(answer, (list, tuple)):
        content = ", ".join(str(a) for a in answer) if answer else "(선택 없음)"
    else:
        content = str(answer)
    user_msg = {"role": "user", "content": content}
    return {"messages": [user_msg], "pending_ask": None}


def _route_after_chat(state: ChatState) -> str:
    return "human" if state.get("pending_ask") else "__end__"


def build_graph():
    g = StateGraph(ChatState)
    g.add_node("chat", _chat_node)
    g.add_node("human", _human_node)
    g.add_edge(START, "chat")
    g.add_conditional_edges(
        "chat", _route_after_chat,
        {"human": "human", "__end__": END},
    )
    g.add_edge("human", "chat")
    return g.compile(checkpointer=MemorySaver())


# ============================================================
# 진입점
# ============================================================
if __name__ == "__main__":
    tracer = Tracer()
    graph = build_graph()
    llm = MockLLM(tracer=tracer)
    launch(graph=graph, llm=llm, tracer=tracer)
