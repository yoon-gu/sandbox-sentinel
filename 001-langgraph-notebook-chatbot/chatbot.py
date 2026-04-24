"""
LangGraph 기반 노트북 멀티턴 챗봇 + self-contained HTML 트레이스 뷰어

원본 출처:
    - langgraph (StateGraph / MemorySaver): https://github.com/langchain-ai/langgraph (MIT)
    - 트레이싱은 LangSmith의 관찰성 개념만 참고하여 독자 재구현 (코드 복제 아님)

라이선스: MIT (langgraph)
생성: Code Conversion Agent

특징
  1) 폐쇄망 친화: 외부 네트워크 호출, 바이너리 파일 저장 없음
  2) 관찰성: 노드/LLM/도구 호출을 계층형 span으로 기록 (토큰, latency 포함)
  3) 반출 편의: 모든 기록은 self-contained HTML 하나로 저장 (JS는 외부 fetch 안 함)
  4) 노트북 지원: IPython.display로 인라인 대화 표시 + 트레이스 미리보기

사용 예시는 파일 하단의 `if __name__ == "__main__":` 블록 또는 ../examples/ 참고.
"""

# ===== 1. Imports =====
import json
import re
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, asdict
from typing import Annotated, Any, Optional, TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import interrupt, Command


# ===== 2. 트레이서 (LangSmith 스타일의 간이 observability) =====

# 현재 실행 중인 span id를 들고 다니는 컨텍스트 변수.
# 자식 span이 start될 때 이 값을 parent_id로 참조한다.
_current_parent: ContextVar[Optional[str]] = ContextVar("current_parent", default=None)


@dataclass
class Span:
    """하나의 실행 구간(노드/LLM 호출/도구 호출 등)을 나타내는 기록 단위."""
    id: str
    parent_id: Optional[str]
    name: str
    kind: str   # "chain" | "llm" | "tool"
    start: float
    end: Optional[float] = None
    inputs: Any = None
    outputs: Any = None
    tokens_in: int = 0
    tokens_out: int = 0
    error: Optional[str] = None
    metadata: dict = field(default_factory=dict)


def _safe_jsonable(x: Any) -> Any:
    """JSON 직렬화 가능 여부를 확인하고, 불가능하면 repr 문자열로 대체한다."""
    try:
        json.dumps(x, ensure_ascii=False, default=str)
        return x
    except Exception:
        return repr(x)


class Tracer:
    """메모리에 span을 누적하고, 요청 시 self-contained HTML로 내보내는 트레이서."""

    def __init__(self):
        self.spans: list[Span] = []

    def start(self, name: str, kind: str = "chain",
              inputs: Any = None, metadata: Optional[dict] = None) -> Span:
        s = Span(
            id=uuid.uuid4().hex[:8],
            parent_id=_current_parent.get(),
            name=name,
            kind=kind,
            start=time.perf_counter(),
            inputs=_safe_jsonable(inputs),
            metadata=metadata or {},
        )
        self.spans.append(s)
        return s

    def finish(self, span: Span, outputs: Any = None,
               tokens_in: int = 0, tokens_out: int = 0,
               error: Optional[str] = None) -> None:
        if span.end is not None:
            return
        span.end = time.perf_counter()
        if outputs is not None:
            span.outputs = _safe_jsonable(outputs)
        span.tokens_in = tokens_in
        span.tokens_out = tokens_out
        span.error = error

    @contextmanager
    def span(self, name: str, kind: str = "chain",
             inputs: Any = None, metadata: Optional[dict] = None):
        """parent 관계를 자동으로 유지하는 컨텍스트 매니저.

        사용 예:
            with tracer.span("LLM", kind="llm", inputs=msgs) as s:
                out = ...
                tracer.finish(s, outputs=out, tokens_in=..., tokens_out=...)
        """
        s = self.start(name, kind, inputs, metadata)
        token = _current_parent.set(s.id)
        try:
            yield s
        except Exception as e:
            self.finish(s, error=f"{type(e).__name__}: {e}")
            raise
        finally:
            _current_parent.reset(token)
            # finish()를 호출하지 않은 경우에도 안전하게 종료 처리
            if s.end is None:
                self.finish(s)

    # ----- HTML export -----
    def to_html(self, title: str = "Trace") -> str:
        """누적된 span들을 self-contained HTML 문자열로 직렬화."""
        data = []
        for s in self.spans:
            d = asdict(s)
            d["latency_ms"] = (s.end - s.start) * 1000.0 if s.end is not None else 0.0
            data.append(d)
        # JSON 내부의 `</` 는 브라우저가 </script>로 오인할 수 있으므로 이스케이프
        payload = json.dumps(data, ensure_ascii=False, default=str).replace("</", "<\\/")
        return (_TRACE_HTML_TEMPLATE
                .replace("{{TITLE}}", _html_escape(title))
                .replace("{{SPANS_JSON}}", payload))

    def save_html(self, path: str, title: str = "Trace") -> str:
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.to_html(title=title))
        return path

    def clear(self) -> None:
        self.spans.clear()

    def summary(self) -> dict:
        """토큰/지연시간 합계 등 간단 통계."""
        llm = [s for s in self.spans if s.kind == "llm"]
        root = [s for s in self.spans if s.parent_id is None]
        return {
            "total_spans": len(self.spans),
            "llm_calls": len(llm),
            "tokens_in": sum(s.tokens_in for s in llm),
            "tokens_out": sum(s.tokens_out for s in llm),
            "root_latency_ms": sum(
                (s.end - s.start) * 1000.0 for s in root if s.end is not None
            ),
        }


def _html_escape(s: str) -> str:
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;"))


# ===== 3. Mock LLM (워크플로 시연용) =====

class MockLLM:
    """외부 모델 의존 없이 동작하는 에코-스타일 LLM 시뮬레이터.

    - 토큰 수: 공백 기준 단어 수 근사
    - 지연 시간: 출력 토큰 수 × per_token_ms
    """

    # 자동 감지 우회 표식: Chatbot.__init__ 의 _looks_like_compiled_graph 가
    # 이 표식을 보면 "이미 dict 계약을 따르는 어댑터" 로 간주하고 그대로 넘긴다.
    _dict_invoke_contract = True

    def __init__(self, name: str = "mock-llm-001",
                 per_token_ms: float = 8.0, tracer: Optional[Tracer] = None):
        self.name = name
        self.per_token_ms = per_token_ms
        self.tracer = tracer

    def invoke(self, messages: list[dict]) -> dict:
        if self.tracer is None:
            reply, _, _ = self._generate(messages)
            return reply
        with self.tracer.span(f"LLM:{self.name}", kind="llm",
                              inputs=messages,
                              metadata={"model": self.name}) as s:
            reply, ti, to = self._generate(messages)
            self.tracer.finish(s, outputs=reply, tokens_in=ti, tokens_out=to)
            return reply

    def _generate(self, messages: list[dict]) -> tuple[dict, int, int]:
        last_user = next(
            (m["content"] for m in reversed(messages) if m.get("role") == "user"),
            "",
        )
        # 바로 직전 메시지가 tool 결과일 때만 "도구 결과를 봤다"는 답을 한다.
        # (과거 턴의 tool 메시지를 엉뚱한 턴에서 다시 참조하지 않도록)
        tail = messages[-1] if messages else {}
        tool_reply = tail.get("content") if tail.get("role") == "tool" else None
        turn = sum(1 for m in messages if m.get("role") == "user")

        # 직전 assistant 턴이 사용자에게 질문(ask_user)을 던졌고, 지금은 그 답변이
        # 들어온 시점 → interrupt 를 해소하는 최종 답변을 생성한다.
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

        # ---- HITL 트리거: LLM 이 스스로 사용자에게 되묻기로 결정 ----
        # 복수선택(multi_choice) 유도 키워드 — 먼저 검사 (객관식보다 더 구체적인 패턴)
        if any(k in last_user for k in ("여러", "복수", "해당", "체크", "모두")):
            question = "관심 있는 항목을 **모두** 체크해주세요."
            options = [
                "주식",
                "채권",
                "부동산",
                "현금성 자산",
                "대체투자 (원자재/헤지펀드)",
            ]
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
        """토큰 수 근사와 latency 시뮬레이션을 공통 처리."""
        tokens_in = sum(len(str(m.get("content", "")).split()) for m in messages)
        tokens_out = len(text.split())
        # latency 시뮬레이션 — 실제 LLM처럼 출력 길이에 비례
        time.sleep(tokens_out * self.per_token_ms / 1000.0)
        return {"role": "assistant", "content": text}, tokens_in, tokens_out


# ===== 3.5 LangChain create_agent / CompiledStateGraph 어댑터 =====
#
# Chatbot 의 LLM 계약은 본래 `invoke(messages: list[dict]) -> dict` 한 가지 이지만,
# `langchain.agents.create_agent(...)` 같은 모던 헬퍼는 LangGraph CompiledStateGraph
# 를 돌려준다. 그 객체는 `agent.invoke({"messages": [BaseMessage,...]})` 형태를 쓰고
# 결과도 `{"messages": [... AIMessage]}` 로 나온다. 사용자에게는 "둘 다 그대로 꽂으세요"
# 라고 하고 싶으니, Chatbot 내부에서 자동 감지해 아래 어댑터로 감싼다.


def _looks_like_compiled_graph(obj) -> bool:
    """langchain create_agent / langgraph CompiledStateGraph 인지 가벼운 duck-type 감지.

    isinstance 체크 대신 인터페이스 형태로 판단해 langgraph/langchain 미설치 환경에서도
    안전하다. 사용자 정의 어댑터에 우연히 같은 메서드들이 있으면, 클래스에
    `_dict_invoke_contract = True` 속성을 두면 자동 감지를 건너뛴다 (MockLLM 참고).
    """
    if getattr(obj, "_dict_invoke_contract", False):
        return False
    return (
        callable(getattr(obj, "invoke", None))
        and callable(getattr(obj, "stream", None))
        and callable(getattr(obj, "get_graph", None))
    )


class _CompiledGraphLLMAdapter:
    """create_agent 결과(LangGraph CompiledStateGraph)를 Chatbot dict-계약에 맞춰주는 어댑터.

    - 입력: list[dict] (role/content) → langchain BaseMessage 리스트로 변환해 내부 그래프에 전달
    - 출력: 내부 그래프가 돌려준 messages 의 마지막 AIMessage 를 dict 로 추출
    - HITL: 마지막 AIMessage 의 tool_calls 에 `ask_user` (또는 `ask_user_tool_name`) 가
      있으면 **실제 실행하지 않고** Chatbot 의 기존 ask_user 페이로드 형식으로 변환한다.
      그러면 외부 그래프의 _route_after_chat → _human_node → interrupt() 흐름이 그대로 발동.
      다음 invoke 호출에서 사용자의 답변을 ToolMessage 로 끼워 넣어 내부 에이전트를 재개한다.

    내부 에이전트는 **stateless** 로 호출한다 (config 미주입). 멀티턴 메모리는 외부
    Chatbot 의 MemorySaver 가 전담하므로, 매 invoke 마다 누적된 messages 전체를 넘긴다.
    """

    # 자동 감지 자기 자신은 듀플 래핑 방지
    _dict_invoke_contract = True

    def __init__(self, agent, *, tracer: Optional[Tracer] = None,
                 name: str = "create_agent",
                 ask_user_tool_name: str = "ask_user"):
        self.agent = agent
        self.tracer = tracer
        self.name = name
        self.ask_user_tool_name = ask_user_tool_name
        # 직전 turn 에 ask_user 가 호출된 경우 그 tool_call 의 id/name 을 보관해 둔다.
        # 다음 invoke 의 마지막 user 메시지를 ToolMessage 로 변환해 끼워넣는 데 사용.
        self._pending_ask_tool_call: Optional[dict] = None

    def invoke(self, messages: list[dict]) -> dict:
        # langchain_core 는 어댑터가 실제 사용될 때만 import (lazy)
        from langchain_core.messages import convert_to_messages, ToolMessage

        # pending tool_call 이 있으면 마지막 user 메시지를 ToolMessage 로 치환해 끼워넣는다.
        if self._pending_ask_tool_call is not None and messages \
                and isinstance(messages[-1], dict) \
                and messages[-1].get("role") == "user":
            lc_msgs = convert_to_messages(messages[:-1])
            lc_msgs.append(ToolMessage(
                content=str(messages[-1].get("content", "")),
                tool_call_id=self._pending_ask_tool_call["id"],
                name=self._pending_ask_tool_call["name"],
            ))
            self._pending_ask_tool_call = None
        else:
            lc_msgs = convert_to_messages(messages)

        if self.tracer is None:
            return self._invoke_inner(lc_msgs)

        with self.tracer.span(f"LLM:{self.name}", kind="llm",
                              inputs=messages,
                              metadata={"model": self.name}) as s:
            reply = self._invoke_inner(lc_msgs)
            tokens_in = sum(len(str(m.get("content", "")).split()) for m in messages)
            tokens_out = len(str(reply.get("content", "")).split())
            self.tracer.finish(s, outputs=reply,
                               tokens_in=tokens_in, tokens_out=tokens_out)
            return reply

    def _invoke_inner(self, lc_msgs: list) -> dict:
        result = self.agent.invoke({"messages": lc_msgs})
        msgs_out = (result or {}).get("messages", []) if isinstance(result, dict) else []

        # 마지막 AIMessage 추출 (BaseMessage 의 .type == "ai" 또는 클래스명 검사)
        last_ai = None
        for m in reversed(msgs_out):
            if getattr(m, "type", None) == "ai" or type(m).__name__ == "AIMessage":
                last_ai = m
                break
        if last_ai is None:
            return {"role": "assistant", "content": ""}

        # ask_user tool_call 인터셉트
        tool_calls = list(getattr(last_ai, "tool_calls", None) or [])
        for tc in tool_calls:
            tc_name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
            if tc_name != self.ask_user_tool_name:
                continue
            tc_args = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", {})
            tc_args = tc_args or {}
            tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
            self._pending_ask_tool_call = {
                "id": tc_id, "name": self.ask_user_tool_name,
            }
            content_text = self._content_to_text(getattr(last_ai, "content", "")) \
                or str(tc_args.get("question", ""))
            return {
                "role": "assistant",
                "content": content_text,
                "ask_user": {
                    "type": tc_args.get("type", "input"),
                    "question": tc_args.get("question", ""),
                    "options": tc_args.get("options"),
                },
            }

        # 일반 AIMessage
        return {
            "role": "assistant",
            "content": self._content_to_text(getattr(last_ai, "content", "")),
        }

    @staticmethod
    def _content_to_text(content: Any) -> str:
        """AIMessage.content 가 str 또는 멀티-블록 list 일 수 있어 평문으로 정리."""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for b in content:
                if isinstance(b, dict) and b.get("type") == "text":
                    parts.append(str(b.get("text", "")))
                elif isinstance(b, str):
                    parts.append(b)
            return "".join(parts)
        return str(content) if content is not None else ""


def make_ask_user_tool():
    """`create_agent(tools=[..., make_ask_user_tool()])` 로 주입할 ask_user 툴.

    에이전트가 사용자에게 되묻고 싶을 때 이 툴을 호출하면, _CompiledGraphLLMAdapter 가
    실제 실행 없이 가로채 Chatbot 의 기존 pending_interrupt 플로우로 변환한다.
    툴 함수 본문은 도달하지 않는 것이 정상이며, 어댑터 외부 경로로 잘못 호출됐을 때만
    RuntimeError 로 명시적으로 알린다.

    필요 패키지: `langchain_core>=0.3` (보통 langchain 설치 시 함께 들어옴).
    """
    from langchain_core.tools import tool

    @tool
    def ask_user(type: str, question: str,
                 options: Optional[list] = None) -> str:
        """사용자에게 직접 되물어 답을 받는다.

        Args:
            type: 'input' (주관식) | 'choice' (단일 선택) | 'multi_choice' (복수 선택)
            question: 사용자에게 보여줄 질문 텍스트
            options: choice / multi_choice 일 때 보여줄 선택지 리스트
        """
        raise RuntimeError(
            "ask_user 툴은 _CompiledGraphLLMAdapter 가 가로채야 합니다 "
            "(직접 실행되면 안 됨). Chatbot(llm=create_agent(...)) 형태로 사용하세요."
        )

    return ask_user


# ===== 4. LangGraph 챗봇 그래프 =====

def _append_messages(left: Optional[list], right: Any) -> list:
    """TypedDict 리듀서: 새 메시지(들)를 기존 리스트 뒤에 이어붙인다."""
    if right is None:
        return left or []
    if not isinstance(right, list):
        right = [right]
    return (left or []) + right


class ChatState(TypedDict, total=False):
    messages: Annotated[list, _append_messages]
    # LLM 이 이번 턴에 사용자에게 되묻고 싶어할 때 여기에 페이로드가 적재된다.
    # 이어지는 human 노드가 이 페이로드로 interrupt() 를 건다. 사용자가 답하면 다시 None 으로 리셋.
    pending_ask: Optional[dict]


# 주의: langgraph는 노드의 두 번째 파라미터를 무(無)타입 혹은 RunnableConfig로 기대합니다.
# `config: dict` 처럼 일반 타입을 붙이면 config가 주입되지 않으니 그대로 둡니다.
def _chat_node(state: ChatState, config) -> dict:
    """한 턴의 처리: (1) 간단한 tool 감지 → 있으면 실행 후 LLM 재호출, (2) 없으면 바로 LLM.

    LLM 응답에 `ask_user` 가 붙어오면 사용자에게 되묻기 위해 pending_ask 를 채워 반환한다.
    이 값은 조건부 엣지에 의해 human 노드로 라우팅된다.
    """
    cfg = (config or {}).get("configurable", {})
    tracer: Optional[Tracer] = cfg.get("tracer")
    llm = cfg.get("llm")
    if llm is None:
        raise RuntimeError("config['configurable']['llm']에 LLM 객체를 주입하세요.")

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
    """사용자에게 되묻고 답을 받을 때까지 그래프를 일시정지한다.

    `interrupt()` 는 첫 실행 때 페이로드를 외부로 surface 하며 실행을 멈추고,
    `Command(resume=value)` 로 재개되면 그 value 를 반환한다. langgraph 의 설계상
    이 노드는 resume 시 처음부터 다시 실행되므로 **interrupt 이전에 부수효과를 두지 않는다**.
    """
    ask = state.get("pending_ask") or {}
    answer = interrupt(ask)  # 반드시 첫 문장 — 이전에 어떤 side-effect 도 두지 말 것

    # 이 아래는 오직 resume 된 실행에서만 닿는다.
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

    # multi_choice 의 경우 answer 가 list/tuple 로 들어온다 — 대화 풍선에는 ", " 로 합쳐 표시
    if isinstance(answer, (list, tuple)):
        content = ", ".join(str(a) for a in answer) if answer else "(선택 없음)"
    else:
        content = str(answer)
    user_msg = {"role": "user", "content": content}
    return {"messages": [user_msg], "pending_ask": None}


def _route_after_chat(state: ChatState) -> str:
    """chat 노드 실행 후 pending_ask 가 있으면 human 노드로, 없으면 종료."""
    return "human" if state.get("pending_ask") else "__end__"


def _looks_like_calc(text: str) -> bool:
    keys = ("계산", "더하", "합계", "더해", "+", "sum")
    return any(k in text for k in keys) and bool(re.search(r"-?\d", text))


def _run_calculator(text: str) -> dict:
    """입력에서 정수만 뽑아 합계를 돌려주는 초간단 도구."""
    nums = [int(n) for n in re.findall(r"-?\d+", text)]
    result = sum(nums) if nums else 0
    content = f"계산기 결과: {result} (입력 숫자: {nums})"
    return {"role": "tool", "name": "calculator", "content": content}


def _build_graph():
    g = StateGraph(ChatState)
    g.add_node("chat", _chat_node)
    g.add_node("human", _human_node)
    g.add_edge(START, "chat")
    # chat 노드 결과에 pending_ask 가 있으면 human 노드로 분기, 없으면 종료
    g.add_conditional_edges("chat", _route_after_chat,
                            {"human": "human", "__end__": END})
    # human 노드는 사용자의 답변을 메시지에 추가한 뒤 다시 chat 으로 돌아와 최종 응답을 생성
    g.add_edge("human", "chat")
    return g.compile(checkpointer=MemorySaver())


# ===== 5. Chatbot 고수준 API =====

class Chatbot:
    """노트북에서 바로 쓸 수 있는 멀티턴 챗봇.

    - thread_id를 유지하므로 `chat()`을 반복 호출해도 대화 맥락이 이어진다.
    - 모든 호출이 Tracer에 기록되며, HTML로 저장하거나 노트북 인라인 표시 가능.
    """

    def __init__(self, llm: Optional[Any] = None,
                 tracer: Optional[Tracer] = None,
                 thread_id: Optional[str] = None):
        self.tracer = tracer if tracer is not None else Tracer()
        # llm 인자 자동 감지:
        #   - None              → MockLLM (기본)
        #   - CompiledStateGraph(=create_agent 결과) → 내부 어댑터로 자동 래핑
        #   - 그 외 dict 계약 어댑터 → 그대로 사용
        if llm is None:
            self.llm = MockLLM(tracer=self.tracer)
        elif _looks_like_compiled_graph(llm):
            self.llm = _CompiledGraphLLMAdapter(llm, tracer=self.tracer)
        else:
            self.llm = llm
        # 외부에서 주입한 LLM이 tracer를 들고 있지 않다면 붙여준다
        if getattr(self.llm, "tracer", None) is None:
            try:
                self.llm.tracer = self.tracer
            except Exception:
                pass
        self.app = _build_graph()
        self.thread_id = thread_id or uuid.uuid4().hex[:8]
        # LLM 이 사용자에게 되묻기로 결정한 경우, 그 페이로드가 여기에 실린다.
        # None 이 아니면 다음 호출은 반드시 resume() 이어야 한다.
        self.pending_interrupt: Optional[dict] = None

    def chat(self, user_message: str) -> str:
        """한 턴의 대화. 최종 assistant 응답 문자열을 반환.

        LLM 이 중간에 사용자에게 되묻기로 결정하면, 그래프가 `interrupt()` 로 일시정지하며
        `self.pending_interrupt` 에 {type, question, options?} 페이로드가 적재된다.
        이 경우 반환되는 문자열은 LLM 이 생성한 **질문 자체**이며, 호출자는 `resume(answer)` 로
        그래프를 이어가야 한다.
        """
        if self.pending_interrupt is not None:
            raise RuntimeError(
                "사용자 응답 대기 중입니다. 먼저 bot.resume(value) 로 인터럽트를 해소해주세요."
            )
        with self.tracer.span(
            f"turn: {user_message[:24]}",
            kind="chain",
            inputs={"user": user_message},
            metadata={"thread_id": self.thread_id},
        ) as s:
            result = self.app.invoke(
                {"messages": [{"role": "user", "content": user_message}]},
                config={"configurable": {
                    "thread_id": self.thread_id,
                    "tracer": self.tracer,
                    "llm": self.llm,
                }},
            )
            assistant, interrupt_payload = self._extract_result(result)
            self.pending_interrupt = interrupt_payload
            self.tracer.finish(s, outputs={
                "assistant": assistant,
                "interrupted": interrupt_payload is not None,
            })
        return assistant

    def resume(self, answer: Any) -> str:
        """`pending_interrupt` 를 사용자 답변(`answer`) 으로 해소하고 그래프를 재개한다.

        - 주관식(type=input) 이면 임의의 문자열, 객관식(type=choice) 이면 options 중 하나,
          복수선택(type=multi_choice) 이면 options 의 부분집합 list.
        - 재개 후에도 LLM 이 다시 되묻기로 결정하면 `pending_interrupt` 가 또 채워질 수 있다.
        """
        if self.pending_interrupt is None:
            raise RuntimeError(
                "해소할 인터럽트가 없습니다. 먼저 bot.chat(...) 을 호출하세요."
            )
        with self.tracer.span(
            f"resume: {str(answer)[:24]}",
            kind="chain",
            inputs={"answer": answer, "ask": self.pending_interrupt},
            metadata={"thread_id": self.thread_id},
        ) as s:
            result = self.app.invoke(
                Command(resume=answer),
                config={"configurable": {
                    "thread_id": self.thread_id,
                    "tracer": self.tracer,
                    "llm": self.llm,
                }},
            )
            assistant, interrupt_payload = self._extract_result(result)
            self.pending_interrupt = interrupt_payload
            self.tracer.finish(s, outputs={
                "assistant": assistant,
                "interrupted": interrupt_payload is not None,
            })
        return assistant

    @staticmethod
    def _extract_result(result: Any) -> tuple[str, Optional[dict]]:
        """invoke 결과에서 (최종 assistant 응답, 인터럽트 페이로드) 를 추출.

        langgraph 는 interrupt 가 발생하면 반환 state 에 `__interrupt__` 키로 Interrupt 객체를
        싣는다(버전에 따라 튜플/리스트). 각 객체의 `.value` 가 우리가 넘겼던 ask 페이로드다.
        """
        if not isinstance(result, dict):
            return "", None
        ints = result.get("__interrupt__")
        interrupt_payload = None
        if ints:
            first = ints[0] if not isinstance(ints, dict) else ints
            interrupt_payload = getattr(first, "value", None)
            if interrupt_payload is None and isinstance(first, dict):
                interrupt_payload = first
        assistant = ""
        for m in reversed(result.get("messages", []) or []):
            if isinstance(m, dict) and m.get("role") == "assistant":
                assistant = m.get("content", "")
                break
        return assistant, interrupt_payload

    def history(self) -> list[dict]:
        """현재 thread의 대화 이력 (checkpointer에 저장된 메시지 리스트)."""
        state = self.app.get_state({"configurable": {"thread_id": self.thread_id}})
        return list(state.values.get("messages", []))

    def reset(self, new_thread_id: Optional[str] = None) -> str:
        """새 thread로 리셋. 기존 Tracer 기록은 유지 (원하면 clear_trace 호출)."""
        self.thread_id = new_thread_id or uuid.uuid4().hex[:8]
        self.pending_interrupt = None
        return self.thread_id

    def clear_trace(self) -> None:
        self.tracer.clear()

    def summary(self) -> dict:
        return self.tracer.summary()

    # ----- 출력 -----
    def save_trace(self, path: str = "trace.html",
                   title: Optional[str] = None) -> str:
        title = title or f"Trace — thread {self.thread_id}"
        return self.tracer.save_html(path, title=title)

    def show_trace(self, title: Optional[str] = None) -> None:
        """Jupyter 셀에 트레이스를 인라인 표시."""
        html = self.tracer.to_html(
            title=title or f"Trace — thread {self.thread_id}"
        )
        _display_html(html)

    def show_history(self) -> None:
        """Jupyter 셀에 대화 이력을 풍선 UI로 표시. IPython이 없으면 print로 대체."""
        msgs = self.history()
        try:
            _display_html(_render_history_html(msgs, thread_id=self.thread_id))
        except _NoIPython:
            for m in msgs:
                print(f"[{m.get('role')}] {m.get('content')}")

    def chat_ui(self):
        """Jupyter 셀 Output 안에서 돌아가는 인터랙티브 채팅 위젯.

        **레이아웃**: 세로 스택 — 대화 이력 / 입력창 / [트레이스 저장 · 새 대화] 버튼 /
        상태 / 트레이스 링크 영역.

        트레이스는 **[트레이스 다운로드]** 버튼을 누르면 결과 영역에 data URL 기반 다운로드
        링크가 표시됩니다. 링크를 클릭하면 브라우저가 `trace_<thread_id>_<ts>.html` 을
        사용자 Downloads 폴더로 내려받고, 이후 더블클릭해 열면 브라우저가 self-contained
        HTML 을 직접 렌더합니다. 파일 시스템에 중간 산출물을 남기지 않으며, Jupyter/Colab/
        JupyterHub 등 모든 환경에서 원격 kernel 여부와 무관하게 동일하게 작동합니다.

        일반 대화에서는 입력창 + 보내기 버튼이 보이지만, LLM 이 사용자에게 되묻기로
        결정하면(= `self.pending_interrupt` 가 채워지면) 입력 영역이 동적으로 교체됩니다.

        - **주관식(type=input)** → Textarea + "답변 제출" 버튼
        - **객관식(type=choice)** → RadioButtons + "답변 제출" 버튼
        - **복수선택(type=multi_choice)** → 여러 Checkbox + "답변 제출" 버튼 (체크한 항목들을 list 로 resume)

        제출하면 `resume(answer)` 를 호출해 그래프를 이어가고, 필요시 다시 되묻기 UI 로
        전환됩니다. 모든 동작은 셀 하나의 output 안에서 완결됩니다.

        필요 패키지: `ipywidgets` (JupyterLab/Notebook에 기본 번들되는 경우가 많음).
        미설치 시 RuntimeError.
        """
        try:
            import ipywidgets as W
            from IPython.display import HTML, display, clear_output
        except ImportError as e:
            raise RuntimeError(
                "chat_ui() 는 ipywidgets 가 필요합니다. "
                "사내 미러에 ipywidgets 가 없다면 show_history()/show_trace() 를 사용하세요."
            ) from e

        tid_label = W.HTML(value=self._thread_label_html())
        status = W.HTML(value="")
        history_area = W.Output(layout=W.Layout(
            border="1px solid #e5e5e5", border_radius="6px",
            max_height="420px", overflow="auto", padding="0",
        ))
        # 트레이스는 파일로 저장 + 클릭 가능한 링크를 이 영역에 표시 (노트북 내 HTML
        # 렌더링 파이프라인의 <script>/스타일 제약을 피하기 위함). 사용자는 링크를
        # 클릭해 브라우저 새 탭에서 JS 가 정상 실행되는 self-contained 트레이스를 본다.
        trace_link_area = W.Output()

        # pending_interrupt 유무에 따라 children 이 매번 교체되는 컨테이너.
        input_container = W.VBox([])

        trace_btn = W.Button(description="트레이스 다운로드",
                             button_style="info", icon="download")
        reset_btn = W.Button(description="새 대화 (thread 리셋)",
                             button_style="warning", icon="refresh")
        toolbar = W.HBox([trace_btn, reset_btn])

        def _render_history():
            with history_area:
                clear_output(wait=True)
                display(HTML(_render_history_html(
                    self.history(), thread_id=self.thread_id)))

        def _update_status_ok():
            s = self.summary()
            status.value = (
                f"<span style='color:#666;font-size:11px'>"
                f"LLM 호출 {s['llm_calls']}회 · "
                f"토큰 in/out {s['tokens_in']}/{s['tokens_out']} · "
                f"누적 latency {s['root_latency_ms']:.0f} ms</span>"
            )

        def _show_error(e: Exception):
            status.value = (
                f"<span style='color:#b91c1c;font-size:11px'>"
                f"에러: {type(e).__name__}: {_html_escape(str(e))}</span>"
            )

        def _build_normal_input():
            # Textarea 대신 Text(단행) 위젯 사용 — Enter 로 송신, '보내기' 버튼은 fallback.
            # 다중라인은 ipywidgets 만으로는 단축키 분기가 불가하므로 단행 입력으로 단순화.
            input_box = W.Text(
                placeholder="메시지를 입력하고 Enter 또는 '보내기' 를 누르세요.",
                layout=W.Layout(width="100%"),
            )
            send_btn = W.Button(description="보내기", button_style="primary",
                                icon="paper-plane")

            def _on_send(_btn=None):
                msg = input_box.value.strip()
                if not msg:
                    return
                send_btn.disabled = True
                status.value = "<span style='color:#888;font-size:11px'>응답 생성 중…</span>"
                try:
                    self.chat(msg)
                    input_box.value = ""
                    _render_history()
                    _update_status_ok()
                except Exception as e:
                    _show_error(e)
                finally:
                    send_btn.disabled = False
                    _refresh_input()

            send_btn.on_click(_on_send)
            # Text.on_submit 은 ipywidgets 8 에서 deprecated 이지만 여전히 동작 (DeprecationWarning).
            # 미래에 제거되더라도 보내기 버튼이 fallback 으로 남는다.
            try:
                input_box.on_submit(_on_send)
            except Exception:
                pass
            return W.VBox([input_box, send_btn])

        def _build_interrupt_input(payload: dict):
            qtype = (payload or {}).get("type", "input")
            question = (payload or {}).get("question", "사용자 응답이 필요합니다.")

            tag = {"choice": "객관식", "multi_choice": "복수선택"}.get(qtype, "주관식")

            # 질문 배너 — 노란색 강조로 "LLM 이 되묻는 중" 을 시각화
            banner = W.HTML(value=(
                f"<div style='background:#fef3c7;border:1px solid #fcd34d;"
                f"padding:10px;border-radius:6px;margin-bottom:8px;"
                f"font-family:-apple-system,BlinkMacSystemFont,\"Segoe UI\",sans-serif'>"
                f"<div style='font-size:11px;color:#92400e;"
                f"text-transform:uppercase;letter-spacing:.4px'>"
                f"🤖 LLM 이 사용자에게 질문 · {tag}</div>"
                f"<div style='font-size:13px;color:#7c2d12;margin-top:4px'>"
                f"{_html_escape(str(question))}</div></div>"
            ))

            submit_btn = W.Button(description="답변 제출",
                                  button_style="success", icon="check")

            if qtype == "multi_choice":
                options = list((payload or {}).get("options") or [])
                if not options:
                    options = ["(옵션 없음)"]
                # 여러 Checkbox 를 VBox 로 묶어 복수선택 UI 를 구성
                checkboxes = [
                    W.Checkbox(value=False, description=opt, indent=False,
                               layout=W.Layout(width="100%", margin="0"))
                    for opt in options
                ]
                hint = W.HTML(value=(
                    "<div style='color:#666;font-size:11px;margin:2px 0 6px 0'>"
                    "✓ 하나 이상 체크하고 '답변 제출' 을 누르세요.</div>"
                ))
                box = W.VBox(checkboxes, layout=W.Layout(
                    border="1px solid #e5e5e5", border_radius="4px",
                    padding="6px 8px", margin="0",
                ))

                def _on_submit(_btn=None):
                    selected = [cb.description for cb in checkboxes if cb.value]
                    if not selected:
                        status.value = ("<span style='color:#b45309;font-size:11px'>"
                                        "최소 1개 이상 체크해주세요.</span>")
                        return
                    submit_btn.disabled = True
                    status.value = ("<span style='color:#888;font-size:11px'>"
                                    "응답 처리 중…</span>")
                    try:
                        self.resume(selected)
                        _render_history()
                        _update_status_ok()
                    except Exception as e:
                        _show_error(e)
                    finally:
                        submit_btn.disabled = False
                        _refresh_input()

                submit_btn.on_click(_on_submit)
                return W.VBox([banner, hint, box, submit_btn])

            if qtype == "choice":
                options = list((payload or {}).get("options") or [])
                if not options:
                    options = ["(옵션 없음)"]
                chooser = W.RadioButtons(
                    options=options,
                    value=options[0],
                    layout=W.Layout(width="100%"),
                    style={"description_width": "0"},
                )

                def _on_submit(_btn=None):
                    answer = chooser.value
                    submit_btn.disabled = True
                    status.value = ("<span style='color:#888;font-size:11px'>"
                                    "응답 처리 중…</span>")
                    try:
                        self.resume(answer)
                        _render_history()
                        _update_status_ok()
                    except Exception as e:
                        _show_error(e)
                    finally:
                        submit_btn.disabled = False
                        _refresh_input()

                submit_btn.on_click(_on_submit)
                return W.VBox([banner, chooser, submit_btn])

            # 주관식 (type == "input") — Text(단행) 위젯, Enter 로 제출
            text_box = W.Text(
                placeholder="답변을 입력하고 Enter 또는 '답변 제출' 을 누르세요.",
                layout=W.Layout(width="100%"),
            )

            def _on_submit(_btn=None):
                answer = text_box.value.strip()
                if not answer:
                    return
                submit_btn.disabled = True
                status.value = ("<span style='color:#888;font-size:11px'>"
                                "응답 처리 중…</span>")
                try:
                    self.resume(answer)
                    _render_history()
                    _update_status_ok()
                except Exception as e:
                    _show_error(e)
                finally:
                    submit_btn.disabled = False
                    _refresh_input()

            submit_btn.on_click(_on_submit)
            try:
                text_box.on_submit(_on_submit)
            except Exception:
                pass
            return W.VBox([banner, text_box, submit_btn])

        def _refresh_input():
            if self.pending_interrupt is None:
                input_container.children = (_build_normal_input(),)
            else:
                input_container.children = (
                    _build_interrupt_input(self.pending_interrupt),
                )

        def _on_trace(_btn):
            """트레이스를 base64 data URL 다운로드 링크로 제공.

            data URL + <a download=...> 조합은 파일 시스템/서버 라우팅과 무관하게
            브라우저가 바로 Downloads 폴더로 파일을 떨어뜨린다. JupyterLab 의 /lab/tree/*
            라우팅, Colab 의 원격 VM, 원격 JupyterHub 모두에서 동일하게 작동.
            사용자는 다운받은 HTML 을 더블클릭하면 브라우저가 직접 렌더 (JS 정상 실행).
            """
            import base64 as _b64
            import datetime as _dt
            ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            filename = f"trace_{self.thread_id}_{ts}.html"
            html_src = self.tracer.to_html(
                title=f"Trace — thread {self.thread_id} @ {ts}"
            )
            data_url = (
                "data:text/html;base64,"
                + _b64.b64encode(html_src.encode("utf-8")).decode("ascii")
            )
            span_count = len(self.tracer.spans)

            with trace_link_area:
                clear_output(wait=True)
                display(HTML(
                    f'<div style="font-size:12px;padding:10px 12px;'
                    f'background:#ecfdf5;border:1px solid #86efac;'
                    f'border-radius:6px;color:#065f46;line-height:1.6">'
                    f'✅ 트레이스 준비됨 · {span_count} spans — '
                    f'<a href="{data_url}" download="{_html_escape(filename)}" '
                    f'style="color:#047857;font-weight:500;background:#fff;'
                    f'padding:4px 10px;border-radius:4px;border:1px solid #86efac;'
                    f'text-decoration:none;display:inline-block;margin:2px 0">'
                    f'💾 {_html_escape(filename)} 다운로드</a>'
                    f'<br><span style="color:#888;font-size:11px">'
                    f'다운로드한 파일을 더블클릭하면 브라우저가 트레이스를 직접 렌더합니다.'
                    f'</span></div>'
                ))

        def _on_reset(_btn):
            self.reset()
            tid_label.value = self._thread_label_html()
            _render_history()
            with trace_link_area:
                clear_output(wait=True)
            _refresh_input()
            status.value = (
                "<span style='color:#047857;font-size:11px'>"
                "새 thread 로 리셋되었습니다.</span>"
            )

        trace_btn.on_click(_on_trace)
        reset_btn.on_click(_on_reset)

        _render_history()
        _refresh_input()

        return W.VBox([
            tid_label,
            history_area,
            input_container,
            toolbar,
            status,
            trace_link_area,
        ])

    def _thread_label_html(self) -> str:
        return (
            f"<div style='font-size:11px;color:#666'>"
            f"thread_id: <code>{_html_escape(self.thread_id)}</code></div>"
        )


class _NoIPython(Exception):
    pass


def _display_html(html: str) -> None:
    try:
        from IPython.display import HTML, display
    except ImportError as e:
        raise _NoIPython() from e
    display(HTML(html))


# ===== 6. HTML 템플릿 (외부 리소스 참조 없는 self-contained) =====

_TRACE_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>{{TITLE}}</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Apple SD Gothic Neo",
         "Malgun Gothic", sans-serif; background:#fafafa; color:#222; margin:0; padding:16px; }
  h1 { font-size:16px; margin:0 0 12px 0; }
  .stats { display:flex; flex-wrap:wrap; gap:12px; padding:12px 16px; background:#fff;
           border:1px solid #e5e5e5; border-radius:6px; margin-bottom:12px; }
  .stat { font-size:11px; color:#666; min-width:90px; }
  .stat b { display:block; font-size:16px; color:#222; margin-top:2px; }
  .tree { background:#fff; border:1px solid #e5e5e5; border-radius:6px; padding:8px 10px; }
  .span { padding:6px 8px; border-left:3px solid #ddd; margin:4px 0; border-radius:3px;
          font-size:12px; }
  .span > .head { display:flex; gap:8px; align-items:center; cursor:pointer;
                  user-select:none; }
  .span.kind-llm   { border-left-color:#6366f1; background:#fafaff; }
  .span.kind-tool  { border-left-color:#10b981; background:#f6fffb; }
  .span.kind-chain { border-left-color:#f59e0b; background:#fffcf3; }
  .badge { font-size:10px; padding:2px 6px; border-radius:3px; background:#eee; color:#444;
           text-transform:uppercase; letter-spacing:.4px; }
  .badge.llm   { background:#eef2ff; color:#4338ca; }
  .badge.tool  { background:#ecfdf5; color:#047857; }
  .badge.chain { background:#fffbeb; color:#b45309; }
  .name { flex:1; font-weight:500; word-break:break-all; }
  .meta { font-size:11px; color:#888; white-space:nowrap; }
  .detail { display:none; margin-top:6px; padding:8px; background:#f3f3f3; border-radius:4px;
            font-family: "SF Mono", Menlo, Consolas, monospace; font-size:11px;
            white-space:pre-wrap; word-break:break-word; max-height:280px; overflow:auto; }
  .span.open > .detail { display:block; }
  .children { margin-left:18px; }
  .err { color:#b91c1c; font-size:11px; margin-top:4px; }
  .bar { height:3px; background:#eee; border-radius:2px; margin-top:4px; overflow:hidden; }
  .bar > span { display:block; height:100%; background:currentColor; opacity:.6; }
  .kind-llm .bar > span { color:#6366f1; }
  .kind-tool .bar > span { color:#10b981; }
  .kind-chain .bar > span { color:#f59e0b; }
  .hint { color:#888; font-size:11px; margin:8px 0; }
</style>
</head>
<body>
<h1>{{TITLE}}</h1>
<div class="stats" id="stats"></div>
<div class="hint">span 헤더를 클릭하면 입출력 상세가 펼쳐집니다. 색상: <b style="color:#6366f1">LLM</b> · <b style="color:#10b981">tool</b> · <b style="color:#f59e0b">chain</b></div>
<div class="tree" id="tree"></div>
<script type="application/json" id="spans-data">{{SPANS_JSON}}</script>
<script>
(function() {
  const raw = document.getElementById('spans-data').textContent;
  const data = JSON.parse(raw);

  // 통계 계산
  let llmCount = 0, ti = 0, to = 0, rootLat = 0;
  data.forEach(s => {
    if (s.kind === 'llm') { llmCount++; ti += s.tokens_in||0; to += s.tokens_out||0; }
    if (!s.parent_id) rootLat += s.latency_ms || 0;
  });
  const stats = document.getElementById('stats');
  stats.innerHTML =
    '<div class="stat">총 span<b>' + data.length + '</b></div>' +
    '<div class="stat">LLM 호출<b>' + llmCount + '</b></div>' +
    '<div class="stat">입력 토큰 합<b>' + ti + '</b></div>' +
    '<div class="stat">출력 토큰 합<b>' + to + '</b></div>' +
    '<div class="stat">최상위 latency 합<b>' + rootLat.toFixed(1) + ' ms</b></div>';

  const maxLat = Math.max(1, ...data.map(s => s.latency_ms || 0));

  const byParent = {};
  data.forEach(s => {
    const key = s.parent_id || '__root__';
    (byParent[key] = byParent[key] || []).push(s);
  });

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  function fmt(v) {
    if (v === null || v === undefined) return '(없음)';
    try { return JSON.stringify(v, null, 2); } catch (e) { return String(v); }
  }

  function render(parentId) {
    const kids = byParent[parentId] || [];
    return kids.map(s => {
      const meta = [];
      meta.push((s.latency_ms || 0).toFixed(1) + ' ms');
      if (s.kind === 'llm') {
        meta.push('in:' + (s.tokens_in || 0) + ' / out:' + (s.tokens_out || 0));
      }
      const pct = ((s.latency_ms || 0) / maxLat * 100).toFixed(1);
      const err = s.error ? '<div class="err">error: ' + esc(s.error) + '</div>' : '';
      return '<div class="span kind-' + esc(s.kind) + '">' +
        '<div class="head" onclick="this.parentNode.classList.toggle(\'open\')">' +
          '<span class="badge ' + esc(s.kind) + '">' + esc(s.kind) + '</span>' +
          '<span class="name">' + esc(s.name) + '</span>' +
          '<span class="meta">' + meta.join(' · ') + '</span>' +
        '</div>' +
        '<div class="bar"><span style="width:' + pct + '%"></span></div>' +
        err +
        '<div class="detail">■ inputs\n' + esc(fmt(s.inputs)) +
          '\n\n■ outputs\n' + esc(fmt(s.outputs)) +
          (s.metadata && Object.keys(s.metadata).length
             ? '\n\n■ metadata\n' + esc(fmt(s.metadata)) : '') +
        '</div>' +
        '<div class="children">' + render(s.id) + '</div>' +
      '</div>';
    }).join('');
  }

  document.getElementById('tree').innerHTML = render('__root__');
})();
</script>
</body>
</html>
"""


def _render_history_html(messages: list[dict], thread_id: str = "") -> str:
    """대화 이력을 채팅 풍선 스타일의 self-contained HTML로 렌더."""
    bubbles = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role", "?")
        content = _html_escape(str(m.get("content", "")))
        cls = {
            "user": "u",
            "assistant": "a",
            "tool": "t",
        }.get(role, "o")
        name_badge = f'<span class="role">{_html_escape(role)}</span>'

        # LLM 이 되묻기 질문을 보낸 경우, 유형/옵션을 시각적으로 함께 표시
        ask = m.get("ask_user") if role == "assistant" else None
        ask_html = ""
        if isinstance(ask, dict):
            qtype = ask.get("type", "input")
            tag = {"choice": "객관식", "multi_choice": "복수선택"}.get(qtype, "주관식")
            opts = ask.get("options") or []
            opts_html = ""
            if opts:
                opts_html = (
                    '<ul class="opts">'
                    + "".join(f"<li>{_html_escape(str(o))}</li>" for o in opts)
                    + "</ul>"
                )
            ask_html = (
                f'<div class="ask">🤚 <b>사용자 응답 요청</b> · {tag}{opts_html}</div>'
            )

        bubbles.append(
            f'<div class="msg {cls}">{name_badge}'
            f'<div class="bubble">{content}{ask_html}</div></div>'
        )
    body = "\n".join(bubbles) if bubbles else '<div class="empty">대화 이력이 비어 있습니다.</div>'
    tid = _html_escape(thread_id)
    return f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<style>
  .chat {{ font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Apple SD Gothic Neo",sans-serif;
           max-width:720px; background:#fff; border:1px solid #e5e5e5; border-radius:8px;
           padding:12px; }}
  .chat h3 {{ font-size:13px; color:#666; margin:0 0 10px 0; font-weight:500; }}
  .msg {{ display:flex; gap:8px; margin:8px 0; align-items:flex-start; }}
  .msg.a {{ flex-direction:row; }}
  .msg.u {{ flex-direction:row-reverse; }}
  .role {{ font-size:10px; padding:2px 6px; border-radius:3px; background:#eee;
           color:#444; text-transform:uppercase; height:fit-content; }}
  .msg.u .role {{ background:#dbeafe; color:#1e40af; }}
  .msg.a .role {{ background:#eef2ff; color:#4338ca; }}
  .msg.t .role {{ background:#ecfdf5; color:#047857; }}
  .bubble {{ padding:8px 12px; border-radius:10px; background:#f3f4f6; max-width:520px;
             white-space:pre-wrap; word-break:break-word; font-size:13px; }}
  .msg.u .bubble {{ background:#dbeafe; }}
  .msg.t .bubble {{ background:#ecfdf5; font-family:"SF Mono",Menlo,monospace; font-size:12px; }}
  .ask {{ margin-top:6px; padding:6px 8px; background:#fef3c7; border:1px solid #fcd34d;
          border-radius:6px; font-size:11px; color:#7c2d12; }}
  .ask .opts {{ margin:4px 0 0 16px; padding:0; font-size:12px; }}
  .ask .opts li {{ margin:2px 0; }}
  .empty {{ color:#888; font-size:12px; padding:20px; text-align:center; }}
</style></head><body><div class="chat">
<h3>대화 이력 (thread: {tid})</h3>
{body}
</div></body></html>"""


# ===== 7. Example Usage =====

if __name__ == "__main__":
    # 폐쇄망에서도 동작하도록 Mock LLM 사용
    bot = Chatbot()

    print("=== 멀티턴 대화 시작 ===")
    print("USER: 안녕, 오늘 기분 어때?")
    print("BOT :", bot.chat("안녕, 오늘 기분 어때?"))

    print("\nUSER: 12 더하기 30 더하기 8을 계산해줘")
    print("BOT :", bot.chat("12 더하기 30 더하기 8을 계산해줘"))

    # ---- HITL 데모: LLM 이 스스로 되묻는 객관식 흐름 ----
    print("\nUSER: 포트폴리오 추천해줘")
    print("BOT :", bot.chat("포트폴리오 추천해줘"))
    if bot.pending_interrupt is not None:
        ask = bot.pending_interrupt
        print(f"  ↳ [INTERRUPT · {ask['type']}] 옵션: {ask.get('options')}")
        # 실제 UI 에서는 사용자 입력을 받겠지만, 여기서는 첫 옵션을 자동 선택
        pick = ask["options"][1]
        print(f"  ↳ 사용자 선택: {pick}")
        print("BOT :", bot.resume(pick))

    print("\n=== 요약 ===")
    print(json.dumps(bot.summary(), ensure_ascii=False, indent=2))

    path = bot.save_trace("trace.html")
    print(f"\n트레이스가 '{path}'에 저장되었습니다. 브라우저로 열어보세요.")
