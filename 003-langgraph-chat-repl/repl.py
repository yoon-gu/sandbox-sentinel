"""
LangGraph 기반 터미널 챗봇 REPL (Textual 풀스크린 TUI)

원본 출처:
    - langgraph (StateGraph / MemorySaver / interrupt / Command): https://github.com/langchain-ai/langgraph (MIT)
    - 터미널 UI 프레임워크: https://github.com/Textualize/textual (MIT)

라이선스: MIT (langgraph)
생성: Code Conversion Agent

개념
----
001-langgraph-notebook-chatbot 의 Jupyter 기반 chat_ui() 를 **터미널 풀스크린 앱** 으로
옮긴 변환물. 외부 네트워크 / 새 서버 프로세스 / 포트 오픈 없이 `python repl.py` 한
줄로 Claude Code 풍 TUI (상단 히스토리 + 상태바 + 하단 입력 + HITL 모달) 를 띄운다.

사용자는 자신이 정의한 **LangGraph 그래프 + LLM 어댑터** 를 `launch(graph, llm)` 에
넘기면 그 그래프를 REPL 로 체험할 수 있다. MockLLM/그래프 구성 예시는 examples/ 참고.

주요 기능
--------
  1) 멀티턴 대화 — MemorySaver + thread_id, 매 턴 상태바가 통계 갱신
  2) HITL — ask_user {type: input|choice|multi_choice} 를 위한 모달 UI 자동 전환
  3) 슬래시 명령 — /new /trace /history /help /quit
  4) 트레이스 — 001 의 Tracer 이식, /trace 로 self-contained HTML 저장
  5) 외부 네트워크 / 바이너리 영속화 / 포트 오픈 0

사용 예시는 파일 하단 `if __name__ == "__main__":` 또는 examples/basic_usage.py 참고.
"""

# ===== 1. Imports =====
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, asdict
from typing import Any, Optional, TYPE_CHECKING

from langgraph.types import Command  # noqa: F401  (사용자 그래프가 import 할 수 있도록 re-export 목적)

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Checkbox,
    Footer,
    Header,
    Input,
    Label,
    RadioButton,
    RadioSet,
    RichLog,
    Static,
    TextArea,
)

if TYPE_CHECKING:
    pass


# ===== 2. Tracer (001 에서 이식, LangSmith 스타일 간이 observability) =====

# 실행 중인 span id 를 들고 다니는 컨텍스트 변수 (부모-자식 관계 유지용).
_current_parent: ContextVar[Optional[str]] = ContextVar("current_parent", default=None)


@dataclass
class Span:
    """실행 구간(노드/LLM/도구) 의 기록 단위."""
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
    """JSON 직렬화 가능 여부 확인 후 불가하면 repr 문자열로 대체."""
    try:
        json.dumps(x, ensure_ascii=False, default=str)
        return x
    except Exception:
        return repr(x)


def _html_escape(s: str) -> str:
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;"))


class Tracer:
    """메모리에 span 누적 + HTML 내보내기."""

    def __init__(self) -> None:
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
        """parent 관계를 자동으로 유지하는 컨텍스트 매니저."""
        s = self.start(name, kind, inputs, metadata)
        token = _current_parent.set(s.id)
        try:
            yield s
        except Exception as e:
            self.finish(s, error=f"{type(e).__name__}: {e}")
            raise
        finally:
            _current_parent.reset(token)
            if s.end is None:
                self.finish(s)

    def to_html(self, title: str = "Trace") -> str:
        """누적 span 을 self-contained HTML 로 직렬화.

        브라우저에서 열 때 JS 가 실행되어 인터랙티브 트리 렌더링이 됨.
        """
        data = []
        for s in self.spans:
            d = asdict(s)
            d["latency_ms"] = (s.end - s.start) * 1000.0 if s.end is not None else 0.0
            data.append(d)
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


# ===== 3. ChatEngine (LangGraph 그래프 + thread 관리, UI 와 분리) =====

class ChatEngine:
    """LangGraph 그래프를 실행하고 HITL 상태를 관리하는 UI 독립적 엔진.

    Textual 앱에서 `engine.chat(...)` / `engine.resume(...)` 만 호출하면 된다.
    """

    def __init__(self, graph: Any, llm: Any, tracer: Optional[Tracer] = None,
                 thread_id: Optional[str] = None) -> None:
        self.graph = graph
        self.llm = llm
        self.tracer = tracer if tracer is not None else Tracer()
        # LLM 어댑터가 tracer 를 갖고 있지 않으면 연결해준다.
        if getattr(self.llm, "tracer", None) is None:
            try:
                self.llm.tracer = self.tracer
            except Exception:
                pass
        self.thread_id: str = thread_id or uuid.uuid4().hex[:8]
        self.pending_interrupt: Optional[dict] = None

    def chat(self, user_message: str) -> tuple[str, Optional[dict]]:
        """한 턴의 대화. (assistant_reply, interrupt_payload) 반환."""
        if self.pending_interrupt is not None:
            raise RuntimeError(
                "HITL 응답 대기 중입니다. 먼저 resume(answer) 로 인터럽트를 해소하세요."
            )
        with self.tracer.span(
            f"turn: {user_message[:24]}",
            kind="chain",
            inputs={"user": user_message},
            metadata={"thread_id": self.thread_id},
        ) as s:
            from langgraph.types import Command as _Cmd  # noqa: F401  (런타임 임포트)
            result = self.graph.invoke(
                {"messages": [{"role": "user", "content": user_message}]},
                config={"configurable": {
                    "thread_id": self.thread_id,
                    "tracer": self.tracer,
                    "llm": self.llm,
                }},
            )
            assistant, interrupt_payload = self._extract(result)
            self.pending_interrupt = interrupt_payload
            self.tracer.finish(s, outputs={
                "assistant": assistant,
                "interrupted": interrupt_payload is not None,
            })
        return assistant, interrupt_payload

    def resume(self, answer: Any) -> tuple[str, Optional[dict]]:
        """pending_interrupt 를 answer 로 해소하고 그래프 재개."""
        if self.pending_interrupt is None:
            raise RuntimeError("해소할 인터럽트가 없습니다. 먼저 chat(...) 을 호출하세요.")
        with self.tracer.span(
            f"resume: {str(answer)[:24]}",
            kind="chain",
            inputs={"answer": answer, "ask": self.pending_interrupt},
            metadata={"thread_id": self.thread_id},
        ) as s:
            from langgraph.types import Command as _Cmd
            result = self.graph.invoke(
                _Cmd(resume=answer),
                config={"configurable": {
                    "thread_id": self.thread_id,
                    "tracer": self.tracer,
                    "llm": self.llm,
                }},
            )
            assistant, interrupt_payload = self._extract(result)
            self.pending_interrupt = interrupt_payload
            self.tracer.finish(s, outputs={
                "assistant": assistant,
                "interrupted": interrupt_payload is not None,
            })
        return assistant, interrupt_payload

    def history(self) -> list[dict]:
        """현재 thread 의 대화 이력."""
        state = self.graph.get_state({"configurable": {"thread_id": self.thread_id}})
        return list(state.values.get("messages", []))

    def reset(self, new_thread_id: Optional[str] = None) -> str:
        """새 thread 로 리셋 (Tracer 는 유지)."""
        self.thread_id = new_thread_id or uuid.uuid4().hex[:8]
        self.pending_interrupt = None
        return self.thread_id

    def summary(self) -> dict:
        return self.tracer.summary()

    @staticmethod
    def _extract(result: Any) -> tuple[str, Optional[dict]]:
        """invoke 결과에서 (assistant_text, interrupt_payload) 추출."""
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


# ===== 4. HITL Modal Screens =====
# 세 가지 HITL 유형별로 모달 화면을 띄운 뒤 사용자 답변을 받아서 ChatApp 에 전달.

class _HITLInputScreen(ModalScreen[Optional[str]]):
    """주관식(type=input) — Textarea + 제출 버튼."""

    DEFAULT_CSS = """
    _HITLInputScreen { align: center middle; }
    _HITLInputScreen > Vertical {
        width: 80; height: auto; padding: 1 2;
        border: thick $warning; background: $surface;
    }
    _HITLInputScreen Label.banner {
        text-style: bold; color: $warning; margin-bottom: 1;
    }
    _HITLInputScreen TextArea { height: 6; margin-bottom: 1; }
    _HITLInputScreen Horizontal { align: right middle; }
    _HITLInputScreen Button { margin-left: 1; }
    """

    BINDINGS = [
        # priority=True 로 widget-level 입력(예: TextArea/Checkbox 자체 keymap) 보다 먼저 매칭
        Binding("escape", "cancel", "취소", show=False, priority=True),
        # Ctrl+S 는 터미널 XON/XOFF 에 잡혀 불가 → Alt+Enter + F2 병행 + 버튼 클릭도 가능
        Binding("alt+enter", "submit", "제출", show=True, priority=True),
        Binding("f2", "submit", "F2 제출", show=False, priority=True),
    ]

    def __init__(self, question: str) -> None:
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(f"🤚 LLM 이 사용자에게 질문 · 주관식", classes="banner")
            yield Label(self.question)
            yield TextArea(id="answer-input")
            with Horizontal():
                yield Button("취소", id="cancel-btn", variant="default")
                yield Button("제출 (Alt+Enter · F2)", id="submit-btn", variant="success")

    def on_mount(self) -> None:
        self.query_one("#answer-input", TextArea).focus()

    def action_submit(self) -> None:
        value = self.query_one("#answer-input", TextArea).text.strip()
        if value:
            self.dismiss(value)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "submit-btn":
            self.action_submit()
        elif event.button.id == "cancel-btn":
            self.action_cancel()


class _HITLChoiceScreen(ModalScreen[Optional[str]]):
    """객관식(type=choice) — RadioSet + 제출."""

    DEFAULT_CSS = """
    _HITLChoiceScreen { align: center middle; }
    _HITLChoiceScreen > Vertical {
        width: 80; height: auto; padding: 1 2;
        border: thick $warning; background: $surface;
    }
    _HITLChoiceScreen Label.banner {
        text-style: bold; color: $warning; margin-bottom: 1;
    }
    _HITLChoiceScreen RadioSet { margin: 1 0; }
    _HITLChoiceScreen Horizontal { align: right middle; }
    _HITLChoiceScreen Button { margin-left: 1; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "취소", show=False),
        # Enter 는 RadioSet 이 흡수하므로 (포커스된 옵션 토글), 제출은 Ctrl+S 로 통일
        Binding("ctrl+s", "submit", "제출", show=True),
    ]

    def __init__(self, question: str, options: list[str]) -> None:
        super().__init__()
        self.question = question
        self.options = options or ["(옵션 없음)"]

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(f"🤚 LLM 이 사용자에게 질문 · 객관식", classes="banner")
            yield Label(self.question)
            with RadioSet(id="choice-set"):
                # 첫 옵션을 기본 선택 (Textual 의 RadioSet.pressed_index 는 read-only
                # property 라 mount 후 setter 할당이 불가. RadioButton value 로 지정.)
                for i, opt in enumerate(self.options):
                    yield RadioButton(opt, value=(i == 0))
            with Horizontal():
                yield Button("취소", id="cancel-btn", variant="default")
                yield Button("제출 (Alt+Enter · F2)", id="submit-btn", variant="success")

    def on_mount(self) -> None:
        self.query_one("#choice-set", RadioSet).focus()

    def action_submit(self) -> None:
        rs = self.query_one("#choice-set", RadioSet)
        idx = rs.pressed_index
        if 0 <= idx < len(self.options):
            self.dismiss(self.options[idx])

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "submit-btn":
            self.action_submit()
        elif event.button.id == "cancel-btn":
            self.action_cancel()


class _HITLMultiChoiceScreen(ModalScreen[Optional[list[str]]]):
    """복수선택(type=multi_choice) — Checkbox 여러 개 + 제출."""

    DEFAULT_CSS = """
    _HITLMultiChoiceScreen { align: center middle; }
    _HITLMultiChoiceScreen > Vertical {
        width: 80; height: auto; padding: 1 2;
        border: thick $warning; background: $surface;
    }
    _HITLMultiChoiceScreen Label.banner {
        text-style: bold; color: $warning; margin-bottom: 1;
    }
    _HITLMultiChoiceScreen VerticalScroll {
        max-height: 12; border: solid $primary; padding: 0 1; margin: 1 0;
    }
    _HITLMultiChoiceScreen Horizontal { align: right middle; }
    _HITLMultiChoiceScreen Button { margin-left: 1; }
    _HITLMultiChoiceScreen Label.hint {
        color: $text-muted; text-style: italic;
    }
    """

    BINDINGS = [
        # priority=True 로 widget-level 입력(예: TextArea/Checkbox 자체 keymap) 보다 먼저 매칭
        Binding("escape", "cancel", "취소", show=False, priority=True),
        # Ctrl+S 는 터미널 XON/XOFF 에 잡혀 불가 → Alt+Enter + F2 병행 + 버튼 클릭도 가능
        Binding("alt+enter", "submit", "제출", show=True, priority=True),
        Binding("f2", "submit", "F2 제출", show=False, priority=True),
    ]

    def __init__(self, question: str, options: list[str]) -> None:
        super().__init__()
        self.question = question
        self.options = options or ["(옵션 없음)"]

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(f"🤚 LLM 이 사용자에게 질문 · 복수선택", classes="banner")
            yield Label(self.question)
            yield Label("✓ 스페이스로 체크, Alt+Enter(또는 F2) 로 제출 — 최소 1개 체크 필요", classes="hint")
            with VerticalScroll():
                for i, opt in enumerate(self.options):
                    yield Checkbox(opt, id=f"cb-{i}")
            with Horizontal():
                yield Button("취소", id="cancel-btn", variant="default")
                yield Button("제출 (Alt+Enter · F2)", id="submit-btn", variant="success")

    def on_mount(self) -> None:
        # 첫 체크박스에 포커스
        try:
            self.query_one("#cb-0", Checkbox).focus()
        except Exception:
            pass

    def _selected(self) -> list[str]:
        selected: list[str] = []
        for i, opt in enumerate(self.options):
            try:
                cb = self.query_one(f"#cb-{i}", Checkbox)
                if cb.value:
                    selected.append(opt)
            except Exception:
                pass
        return selected

    def action_submit(self) -> None:
        selected = self._selected()
        if selected:
            self.dismiss(selected)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "submit-btn":
            self.action_submit()
        elif event.button.id == "cancel-btn":
            self.action_cancel()


class _ToolDetailsScreen(ModalScreen[None]):
    """F3 으로 띄우는 tool 호출 상세 뷰어.

    현재 Tracer 에 쌓인 모든 kind='tool' span 을 inputs/outputs/metadata JSON 까지
    펼쳐 보여준다. RichLog 기반이라 PageUp/PageDown 등 스크롤 가능.
    """

    DEFAULT_CSS = """
    _ToolDetailsScreen { align: center middle; }
    _ToolDetailsScreen > Vertical {
        width: 110; height: 36; padding: 1 2;
        border: thick $success; background: $surface;
    }
    _ToolDetailsScreen Label.title {
        text-style: bold; color: $success; margin-bottom: 1;
    }
    _ToolDetailsScreen RichLog {
        height: 1fr; border: solid $primary; padding: 0 1;
    }
    _ToolDetailsScreen Horizontal { align: right middle; margin-top: 1; }
    _ToolDetailsScreen Button { margin-left: 1; }
    """

    BINDINGS = [
        Binding("escape", "close", "닫기", show=True),
        Binding("q", "close", "닫기", show=False),
    ]

    def __init__(self, tool_spans: list[Span]) -> None:
        super().__init__()
        self.tool_spans = tool_spans

    def compose(self) -> ComposeResult:
        title = f"⚙ Tool 호출 상세 · {len(self.tool_spans)}건 · ESC 로 닫기"
        with Vertical():
            yield Label(title, classes="title")
            yield RichLog(id="tool-log", highlight=True, markup=True, wrap=True, auto_scroll=False)
            with Horizontal():
                yield Button("닫기 (ESC)", id="close-btn", variant="default")

    def on_mount(self) -> None:
        log = self.query_one("#tool-log", RichLog)
        if not self.tool_spans:
            log.write("[dim](tool 호출 기록이 없습니다 — 'calculator' 같은 키워드로 tool 을 먼저 호출해보세요)[/dim]")
            return
        for i, s in enumerate(self.tool_spans, 1):
            latency = (s.end - s.start) * 1000.0 if s.end is not None else 0.0
            log.write(f"[bold green]#{i}  {s.name}[/bold green]  [dim]· {latency:.1f} ms[/dim]")
            log.write(f"[yellow]■ inputs[/yellow]")
            log.write(self._fmt(s.inputs))
            log.write(f"[yellow]■ outputs[/yellow]")
            log.write(self._fmt(s.outputs))
            if s.metadata:
                log.write(f"[yellow]■ metadata[/yellow]")
                log.write(self._fmt(s.metadata))
            if s.error:
                log.write(f"[red]■ error[/red]  {s.error}")
            log.write("")  # 구분

    @staticmethod
    def _fmt(v: Any) -> str:
        if v is None:
            return "  [dim](없음)[/dim]"
        try:
            pretty = json.dumps(v, ensure_ascii=False, indent=2, default=str)
        except Exception:
            pretty = str(v)
        # 각 줄 앞에 2칸 들여쓰기
        return "\n".join("  " + ln for ln in pretty.splitlines())

    def action_close(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close-btn":
            self.action_close()


# ===== 5. 메인 Textual 앱 =====

_SLASH_COMMANDS = ("/new", "/trace", "/history", "/help", "/quit")


class ChatApp(App[None]):
    """LangGraph 챗봇 REPL — Claude Code 스타일 풀스크린 TUI.

    상단: 대화 히스토리 (스크롤) · 상태바 · 하단: 입력창
    HITL 는 모달 화면으로 띄움 (주관식=TextArea / 객관식=RadioSet / 복수선택=Checkbox)
    """

    CSS = """
    Screen { layout: vertical; }
    Header { background: $primary-background; }
    #history {
        height: 1fr;
        border: solid $primary-darken-2;
        padding: 0 1;
    }
    #status {
        height: 1;
        background: $panel;
        color: $text-muted;
        padding: 0 1;
    }
    #input {
        height: 3;
        margin: 0 0 0 0;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "종료"),
        Binding("ctrl+n", "cmd_new", "새 대화"),
        Binding("ctrl+t", "cmd_trace", "트레이스 저장"),
        Binding("f3", "cmd_tool_details", "Tool 상세"),
        Binding("f1", "cmd_help", "도움말"),
    ]

    TITLE = "LangGraph Chat REPL"

    def __init__(self, engine: ChatEngine) -> None:
        super().__init__()
        self.engine = engine

    # ----- 레이아웃 구성 -----
    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield RichLog(id="history", highlight=True, markup=True, wrap=True, auto_scroll=True)
        yield Static(id="status")
        yield Input(
            id="input",
            placeholder="메시지 입력 · /help · Ctrl+N=새 대화 · Ctrl+T=트레이스 · F3=Tool 상세 · F1=도움말",
        )
        yield Footer()

    def on_mount(self) -> None:
        self._write_banner()
        self._update_status()
        self.query_one(Input).focus()

    # ----- 입력 처리 -----
    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        # 입력 비우기 (이후 처리 중에도 다음 입력 받을 수 있게)
        self.query_one(Input).value = ""
        if not text:
            return
        if text.startswith("/"):
            self._handle_command(text)
            return
        # 일반 메시지 → 그래프 실행
        self._submit_chat(text)

    # ----- 슬래시 명령 -----
    def _handle_command(self, cmd: str) -> None:
        head = cmd.split()[0].lower()
        if head == "/quit":
            self.exit()
        elif head == "/new":
            self.action_cmd_new()
        elif head == "/trace":
            self.action_cmd_trace()
        elif head == "/history":
            self._show_history()
        elif head == "/help":
            self.action_cmd_help()
        else:
            self._write_system(f"알 수 없는 명령: [red]{cmd}[/red] — /help 로 명령어 확인")

    def action_cmd_new(self) -> None:
        new_id = self.engine.reset()
        self.query_one(RichLog).clear()
        self._write_banner()
        self._write_system(f"새 thread 로 리셋됨 → [cyan]{new_id}[/cyan]")
        self._update_status()

    def action_cmd_trace(self) -> None:
        ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"trace_{self.engine.thread_id}_{ts}.html"
        self.engine.tracer.save_html(
            filename,
            title=f"Trace — thread {self.engine.thread_id} @ {ts}",
        )
        abs_path = os.path.abspath(filename)
        span_count = len(self.engine.tracer.spans)
        self._write_system(
            f"✓ 트레이스 저장됨 · {span_count} spans\n"
            f"  경로: [green]{abs_path}[/green]\n"
            f"  브라우저로 열면 span 트리 확인 가능 (self-contained HTML)"
        )

    def action_cmd_help(self) -> None:
        help_text = (
            "[bold]슬래시 명령 · 단축키[/bold]\n"
            "  [cyan]/new[/cyan]      새 thread 로 리셋 (맥락 끊기, Tracer 유지)  — Ctrl+N\n"
            "  [cyan]/trace[/cyan]    현재 트레이스를 HTML 파일로 저장            — Ctrl+T\n"
            "  [cyan]/history[/cyan]  현재 thread 의 대화 이력을 다시 출력\n"
            "  [cyan]Tool 상세[/cyan]  최근 tool 호출들의 inputs/outputs/meta 모달 — [yellow]F3[/yellow]\n"
            "  [cyan]/help[/cyan]     이 도움말                                    — F1\n"
            "  [cyan]/quit[/cyan]     종료                                         — Ctrl+C\n\n"
            "[bold]HITL 모달 안에서[/bold]\n"
            "  제출: [yellow]Alt+Enter[/yellow] 또는 [yellow]F2[/yellow] (터미널에서 Ctrl+S 는 XON/XOFF 에 잡혀 안 됨)\n"
            "  취소: Escape\n"
            "  객관식: ↑/↓ 로 이동, 복수선택: Space 로 체크 토글\n\n"
            "[bold]HITL 트리거 (MockLLM 기준)[/bold]\n"
            "  복수선택: 여러, 복수, 해당, 체크, 모두\n"
            "  객관식  : 추천, 고를, 고르, 골라, 선택지, 옵션\n"
            "  주관식  : 설명해, 알려줘, 명확, 모호, 구체적"
        )
        self._write_system(help_text)

    def action_cmd_tool_details(self) -> None:
        """F3 — 실제 tool 호출 (name 이 'tool:' 로 시작하는 span) 만 모달 표시.

        `kind=="tool"` 에는 HITL 응답(`human:answered`) 도 포함되지만 사용자 요청은
        "도구 설명" 이므로 name prefix 로 좁혀서 실제 외부 도구 호출만 노출.
        """
        tool_spans = [
            s for s in self.engine.tracer.spans
            if s.kind == "tool" and s.name.startswith("tool:")
        ]
        self.push_screen(_ToolDetailsScreen(tool_spans))

    # ----- 그래프 실행 (worker 로 백그라운드 실행) -----
    def _submit_chat(self, text: str) -> None:
        self._write_user(text)
        self._set_status_busy("응답 생성 중…")
        # 그래프 호출을 워커 스레드로 — UI 프리즈 방지
        self.run_worker(self._run_chat_worker(text), exclusive=True, thread=True)

    def _submit_resume(self, answer: Any) -> None:
        # 사용자 응답을 대화에 한 줄 추가 (list 면 합쳐서)
        if isinstance(answer, (list, tuple)):
            display = ", ".join(str(a) for a in answer)
        else:
            display = str(answer)
        self._write_user(display, label="answer")
        self._set_status_busy("응답 처리 중…")
        self.run_worker(self._run_resume_worker(answer), exclusive=True, thread=True)

    async def _run_chat_worker(self, text: str) -> None:
        try:
            assistant, interrupt_payload = self.engine.chat(text)
            self.call_from_thread(self._on_turn_result, assistant, interrupt_payload)
        except Exception as e:
            self.call_from_thread(self._on_turn_error, e)

    async def _run_resume_worker(self, answer: Any) -> None:
        try:
            assistant, interrupt_payload = self.engine.resume(answer)
            self.call_from_thread(self._on_turn_result, assistant, interrupt_payload)
        except Exception as e:
            self.call_from_thread(self._on_turn_error, e)

    # ----- 워커 결과 처리 (메인 스레드) -----
    def _on_turn_result(self, assistant: str, interrupt_payload: Optional[dict]) -> None:
        # tool 스팬이 새로 쌓였으면 표시 (간이 — 마지막 tool span 1건만)
        self._render_recent_tool_calls()
        self._write_assistant(assistant)
        self._update_status()
        if interrupt_payload:
            self._open_hitl(interrupt_payload)

    def _on_turn_error(self, e: Exception) -> None:
        self._write_error(f"{type(e).__name__}: {e}")
        self._update_status()

    # ----- HITL 모달 오픈 -----
    def _open_hitl(self, payload: dict) -> None:
        qtype = payload.get("type", "input")
        question = payload.get("question", "사용자 응답이 필요합니다.")
        options = list(payload.get("options") or [])

        def _after(answer: Any) -> None:
            if answer is None:
                # 사용자가 취소 — pending_interrupt 는 유지되므로 안내만 표시
                self._write_system(
                    "[yellow]HITL 취소됨. 그래프는 여전히 사용자 응답을 기다리는 중입니다. "
                    "다시 이어가려면 /help 의 HITL 트리거로 재시도하거나 /new 로 리셋하세요.[/yellow]"
                )
                return
            self._submit_resume(answer)

        if qtype == "choice":
            self.push_screen(_HITLChoiceScreen(question, options), _after)
        elif qtype == "multi_choice":
            self.push_screen(_HITLMultiChoiceScreen(question, options), _after)
        else:
            self.push_screen(_HITLInputScreen(question), _after)

    # ----- 렌더링 헬퍼 -----
    def _log(self) -> RichLog:
        return self.query_one("#history", RichLog)

    def _write_banner(self) -> None:
        log = self._log()
        log.write(
            f"[bold cyan]╭─ LangGraph Chat REPL ·[/bold cyan] "
            f"thread: [cyan]{self.engine.thread_id}[/cyan] "
            f"[bold cyan]────────────────────────────────╮[/bold cyan]"
        )
        log.write(
            "[dim]  /help 로 명령어 확인 · Ctrl+C 로 종료 · HITL 은 모달로 표시됩니다[/dim]"
        )
        log.write(
            f"[bold cyan]╰────────────────────────────────────────────────────────────────╯[/bold cyan]"
        )
        log.write("")

    def _write_user(self, text: str, label: str = "you") -> None:
        self._log().write(f"[bold green]● {label}[/bold green]  {text}")

    def _write_assistant(self, text: str) -> None:
        # 여러 줄일 경우 각 줄에 들여쓰기
        lines = (text or "").splitlines() or [""]
        self._log().write(f"[bold cyan]● bot[/bold cyan]  {lines[0]}")
        for ln in lines[1:]:
            self._log().write(f"         {ln}")

    def _write_tool(self, text: str) -> None:
        self._log().write(f"  [bold yellow]⚙ tool[/bold yellow]  {text}")

    def _write_system(self, text: str) -> None:
        self._log().write(f"[dim]● system[/dim]  {text}")

    def _write_error(self, text: str) -> None:
        self._log().write(f"[bold red]● error[/bold red]  {text}")

    def _render_recent_tool_calls(self) -> None:
        """직전 턴에서 생긴 tool span 을 한 줄씩 표시."""
        # 마지막 "turn" chain span 을 찾아 그 이후의 tool span 들을 출력
        spans = self.engine.tracer.spans
        if not spans:
            return
        # 가장 최근 루트 chain span 이후의 tool span 만
        last_root_start_idx = 0
        for i in range(len(spans) - 1, -1, -1):
            if spans[i].parent_id is None and spans[i].kind == "chain":
                last_root_start_idx = i
                break
        recent_tools = [
            s for s in spans[last_root_start_idx:]
            if s.kind == "tool"
        ]
        for s in recent_tools:
            out = s.outputs
            if isinstance(out, dict):
                summary = out.get("content") or out.get("answer") or str(out)
            else:
                summary = str(out) if out is not None else ""
            self._write_tool(f"{s.name} → {str(summary)[:100]}")

    def _show_history(self) -> None:
        msgs = self.engine.history()
        if not msgs:
            self._write_system("[dim](대화 이력 없음)[/dim]")
            return
        self._write_system(f"대화 이력 ({len(msgs)} 개 메시지):")
        for m in msgs:
            if not isinstance(m, dict):
                continue
            role = m.get("role", "?")
            content = m.get("content", "")
            if role == "user":
                self._write_user(content)
            elif role == "assistant":
                self._write_assistant(content)
            elif role == "tool":
                self._write_tool(content)
            else:
                self._write_system(f"[{role}] {content}")

    def _set_status_busy(self, msg: str) -> None:
        self.query_one("#status", Static).update(
            f"[yellow]⏳ {msg}[/yellow]"
        )

    def _update_status(self) -> None:
        s = self.engine.summary()
        parts = [
            f"thread: [cyan]{self.engine.thread_id}[/cyan]",
            f"LLM {s['llm_calls']}회",
            f"tokens in/out {s['tokens_in']}/{s['tokens_out']}",
            f"latency {s['root_latency_ms']:.0f} ms",
        ]
        if self.engine.pending_interrupt is not None:
            parts.append("[yellow]HITL 대기[/yellow]")
        self.query_one("#status", Static).update("  ·  ".join(parts))


# ===== 6. Public entry — launch() =====

def launch(graph: Any, llm: Any, tracer: Optional[Tracer] = None,
           thread_id: Optional[str] = None) -> None:
    """사용자 그래프 + LLM 을 REPL 로 기동.

    사용 예:
        from langgraph.graph import StateGraph
        graph = StateGraph(...).compile(checkpointer=MemorySaver())
        launch(graph, my_llm_adapter)
    """
    engine = ChatEngine(graph=graph, llm=llm, tracer=tracer, thread_id=thread_id)
    app = ChatApp(engine)
    app.run()


# ===== 7. HTML 템플릿 (001 에서 그대로 이식, self-contained) =====

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


# ===== 8. __main__ — 없음. 본 파일은 라이브러리. examples/ 참고 =====

if __name__ == "__main__":
    print(
        "이 파일은 라이브러리입니다. 사용 예시는 examples/basic_usage.py 를 참고하세요.\n"
        "  python examples/basic_usage.py"
    )
