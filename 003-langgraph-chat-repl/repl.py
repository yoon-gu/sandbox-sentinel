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
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    OptionList,
    RichLog,
    Static,
)
from rich.text import Text

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


# ===== 4. 인라인 HITL 위젯 =====
# HITL 유형별로 입력 영역을 그대로 스왑하는 방식. 모달 팝업을 쓰지 않아
# Claude Code 스타일의 인라인 대화 UX 를 제공한다.
#   · 객관식 (choice) → Textual 기본 OptionList (↑↓ 이동 · Enter 선택)
#   · 복수선택 (multi_choice) → 커스텀 _InlineMultiChoice 위젯
#       (↑↓ 이동 · Space 토글 · Enter 제출)
#   · 주관식 (input) → Input 위젯 (Enter 제출)
# 세 경우 모두 Esc 로 취소 가능.


class _InlineMultiChoice(Widget):
    """인라인 복수선택 리스트.

    체크박스 그룹을 한 줄씩 Rich Text 로 직접 렌더해서 포커스 커서(▸) / 토글 상태
    ([ ] / [x]) 를 표시한다. Textual 기본 SelectionList 의 Enter 동작(토글) 을
    피하고 Enter 를 '제출' 으로 쓸 수 있도록 커스텀 위젯으로 만듬.
    """

    can_focus = True

    DEFAULT_CSS = """
    _InlineMultiChoice {
        height: auto; min-height: 3; max-height: 10;
        padding: 0 1; border: solid ;
    }
    _InlineMultiChoice:focus { border: solid ; }
    """

    BINDINGS = [
        Binding("up", "cursor_up", "위", show=False),
        Binding("down", "cursor_down", "아래", show=False),
        Binding("space", "toggle", "체크", show=False),
        Binding("enter", "submit", "제출", show=False, priority=True),
        Binding("escape", "cancel", "취소", show=False, priority=True),
    ]

    class Submitted(Message):
        """사용자가 Enter 로 현재 체크 상태를 제출."""
        def __init__(self, values: list[str]) -> None:
            super().__init__()
            self.values = values

    class Cancelled(Message):
        """사용자가 Esc 로 취소."""

    def __init__(self, options: list[str], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.options = options or ["(옵션 없음)"]
        self.selected_flags: list[bool] = [False] * len(self.options)
        self.focused_idx: int = 0

    def render(self) -> Text:
        text = Text()
        for i, opt in enumerate(self.options):
            marker = "▸" if i == self.focused_idx else " "
            check = "[x]" if self.selected_flags[i] else "[ ]"
            style = "bold cyan" if i == self.focused_idx else ""
            text.append(f"{marker} {check} {opt}\n", style=style)
        return text

    def action_cursor_up(self) -> None:
        if self.focused_idx > 0:
            self.focused_idx -= 1
            self.refresh()

    def action_cursor_down(self) -> None:
        if self.focused_idx < len(self.options) - 1:
            self.focused_idx += 1
            self.refresh()

    def action_toggle(self) -> None:
        self.selected_flags[self.focused_idx] = not self.selected_flags[self.focused_idx]
        self.refresh()

    def action_submit(self) -> None:
        selected = [self.options[i] for i, flag in enumerate(self.selected_flags) if flag]
        self.post_message(self.Submitted(selected))

    def action_cancel(self) -> None:
        self.post_message(self.Cancelled())


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


# ===== 5. 메인 Textual 앱 (인라인 HITL 버전) =====

_SLASH_COMMANDS = ("/new", "/trace", "/history", "/tool", "/help", "/quit")

# 슬래시 팔레트 표시용 — (명령, 짧은 설명)
_SLASH_PALETTE: tuple[tuple[str, str], ...] = (
    ("/new", "새 thread 로 리셋 (Ctrl+N)"),
    ("/trace", "트레이스 HTML 저장 (Ctrl+T)"),
    ("/history", "대화 이력 다시 출력"),
    ("/tool", "Tool 호출 상세 모달 (F3)"),
    ("/help", "도움말 (F1)"),
    ("/quit", "종료 (Ctrl+C)"),
)


class ChatApp(App[None]):
    """LangGraph 챗봇 REPL — Claude Code 스타일 풀스크린 TUI.

    레이아웃 (세로):
      · Header
      · RichLog  — 대화 히스토리 (스크롤 가능)
      · Static   — 상태바
      · Static   — HITL 배너 (기본 hidden, HITL 활성화 시 노출)
      · Container — 입력 영역 (모드별로 children 스왑)
          · 일반 대화: Input
          · 객관식 HITL: OptionList (↑↓ 이동, Enter 선택)
          · 복수선택 HITL: _InlineMultiChoice (↑↓ 이동, Space 토글, Enter 제출)
          · 주관식 HITL: Input (Enter 제출, placeholder=질문)
      · Footer

    모든 HITL 인터랙션은 Enter / ↑↓ / Space / Esc 만 사용 — 팝업 모달 없음.
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
    #hitl-banner {
        height: 2;
        padding: 0 1;
        background: $warning 20%;
        color: $warning;
        text-style: bold;
    }
    #hitl-banner.hidden { display: none; }
    #slash-hint {
        height: auto; max-height: 8;
        padding: 0 1;
        background: $panel;
        color: $text;
        border-left: thick $accent;
    }
    #slash-hint.hidden { display: none; }
    #input-wrap {
        height: auto; min-height: 3; max-height: 12;
        padding: 0;
    }
    #main-input, #hitl-input { margin: 0; }
    #hitl-choice, #hitl-multi {
        height: auto; max-height: 10;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "종료"),
        Binding("ctrl+n", "cmd_new", "새 대화"),
        Binding("ctrl+t", "cmd_trace", "트레이스"),
        Binding("f3", "cmd_tool_details", "Tool 상세"),
        Binding("f1", "cmd_help", "도움말"),
        # Esc 는 HITL 활성화 상태에서만 동작 (일반 모드에서는 무시)
        Binding("escape", "cancel_hitl", "HITL 취소", show=False),
        # Tab 은 main-input 이 '/' 로 시작하는 일반 모드일 때만 자동완성 (check_action 조건)
        Binding("tab", "slash_autocomplete", "슬래시 자동완성", show=False, priority=True),
    ]

    def check_action(self, action: str, parameters: tuple) -> Optional[bool]:
        """Tab 바인딩을 조건부로만 활성화해서, 그 외엔 Input 의 기본 Tab(포커스 이동) 유지."""
        if action == "slash_autocomplete":
            try:
                main = self.query_one("#main-input", Input)
            except Exception:
                return False
            return (
                self._hitl_mode is None
                and main.has_focus
                and main.value.startswith("/")
            )
        return True

    def action_slash_autocomplete(self) -> None:
        main = self.query_one("#main-input", Input)
        lower = main.value.lower()
        matches = [cmd for cmd, _ in _SLASH_PALETTE if cmd.startswith(lower)]
        if matches:
            main.value = matches[0]
            main.cursor_position = len(main.value)

    TITLE = "LangGraph Chat REPL"

    def __init__(self, engine: ChatEngine) -> None:
        super().__init__()
        self.engine = engine
        self._hitl_mode: Optional[str] = None  # None | "choice" | "multi" | "input"

    # ----- 레이아웃 구성 -----
    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield RichLog(id="history", highlight=True, markup=True, wrap=True, auto_scroll=True)
        yield Static(id="status")
        yield Static("", id="hitl-banner", classes="hidden")
        # 슬래시 팔레트 — 사용자가 '/' 로 시작하는 입력을 하면 인라인 힌트로 노출
        yield Static("", id="slash-hint", classes="hidden")
        # main-input 은 항상 DOM 에 존재 (HITL 모드에서 display:none 으로 숨김).
        # HITL 위젯은 _enter_hitl 시 동적으로 mount, _enter_normal_mode 시 remove.
        with Container(id="input-wrap"):
            yield Input(
                id="main-input",
                placeholder="메시지 입력 · /help · ↑↓=선택 · Enter=전송 · Esc=HITL 취소 · Tab=슬래시 자동완성",
            )
        yield Footer()

    def on_mount(self) -> None:
        self._write_banner()
        self._update_status()
        self._enter_normal_mode()

    # ----- 모드 전환 -----
    def _remove_hitl_widgets(self) -> None:
        """HITL 위젯(hitl-choice/hitl-multi/hitl-input) 이 있으면 제거."""
        for wid_id in ("hitl-choice", "hitl-multi", "hitl-input"):
            try:
                self.query_one(f"#{wid_id}").remove()
            except Exception:
                pass

    def _show_main_input(self, show: bool) -> None:
        main = self.query_one("#main-input", Input)
        main.display = show
        if show:
            main.value = ""
            main.focus()

    def _enter_normal_mode(self) -> None:
        self._hitl_mode = None
        self.query_one("#hitl-banner", Static).add_class("hidden")
        self._remove_hitl_widgets()
        self._show_main_input(True)

    def _enter_hitl(self, payload: dict) -> None:
        qtype = payload.get("type", "input")
        question = str(payload.get("question") or "")
        options = list(payload.get("options") or [])
        # HITL 진입 시 슬래시 힌트는 숨긴다 (일반 모드로 돌아가면 다시 Input.Changed 가 복원)
        self._hide_slash_hint()

        label = {"choice": "객관식", "multi_choice": "복수선택"}.get(qtype, "주관식")
        hints = {
            "choice": "↑↓ 이동 · Enter 선택 · Esc 취소",
            "multi_choice": "↑↓ 이동 · Space 체크 · Enter 제출 · Esc 취소",
            "input": "Enter 제출 · Esc 취소",
        }[qtype]
        banner = self.query_one("#hitl-banner", Static)
        banner.remove_class("hidden")
        banner.update(
            f"🤚 [bold yellow]{label}[/bold yellow]  {question}\n"
            f"[dim]{hints}[/dim]"
        )

        # 이전 HITL 위젯 제거 + main-input 숨김
        self._remove_hitl_widgets()
        self._show_main_input(False)

        wrap = self.query_one("#input-wrap", Container)
        if qtype == "choice":
            self._hitl_mode = "choice"
            opts = options or ["(옵션 없음)"]
            widget = OptionList(*opts, id="hitl-choice")
            wrap.mount(widget)
            widget.focus()
        elif qtype == "multi_choice":
            self._hitl_mode = "multi"
            widget = _InlineMultiChoice(options, id="hitl-multi")
            wrap.mount(widget)
            widget.focus()
        else:  # input (주관식)
            self._hitl_mode = "input"
            widget = Input(id="hitl-input", placeholder=f"답변 입력 후 Enter — {question}")
            wrap.mount(widget)
            widget.focus()

    def action_cancel_hitl(self) -> None:
        """Esc — HITL 모드일 때만 취소 (그래프의 pending_interrupt 는 유지)."""
        if self._hitl_mode is None:
            return
        self._write_system(
            "[yellow]HITL 취소. 그래프는 여전히 사용자 응답 대기 중입니다. "
            "같은 트리거 문구를 다시 보내거나 /new 로 리셋하세요.[/yellow]"
        )
        # 일반 모드로 복귀하지 않고, 일단 입력창을 일반 Input 으로 되돌린다.
        # 하지만 pending_interrupt 는 엔진에 남아있으므로 다음 chat() 에서 에러 발생 가능.
        # → 사용자에게 명확히 안내하고 normal mode 로 전환.
        self._enter_normal_mode()

    # ----- 입력 처리 -----
    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Input 위젯의 Enter — id 로 일반 vs HITL 분기."""
        value = event.value.strip()
        event.input.value = ""
        if event.input.id == "main-input":
            self._hide_slash_hint()
            if not value:
                return
            if value.startswith("/"):
                self._handle_command(value)
            else:
                self._submit_chat(value)
        elif event.input.id == "hitl-input":
            if not value:
                return
            self._submit_resume(value)
            # 모드 전환은 worker 결과 도착 시 _on_turn_result 가 처리

    def on_input_changed(self, event: Input.Changed) -> None:
        """Main input 이 '/' 로 시작하면 슬래시 팔레트 힌트를 인라인 노출."""
        if event.input.id != "main-input":
            return
        value = event.value
        if value.startswith("/"):
            self._show_slash_hint(value)
        else:
            self._hide_slash_hint()

    def _show_slash_hint(self, prefix: str) -> None:
        hint = self.query_one("#slash-hint", Static)
        lower = prefix.lower()
        matches = [(cmd, desc) for cmd, desc in _SLASH_PALETTE if cmd.startswith(lower)]
        if not matches:
            hint.update(
                f"[dim]알려진 슬래시 명령이 없습니다. Tab 으로 첫 매치 자동완성, "
                f"또는 다음 중 선택:[/dim]\n"
                + "  ".join(f"[cyan]{cmd}[/cyan]" for cmd, _ in _SLASH_PALETTE)
            )
            hint.remove_class("hidden")
            return
        lines = [
            f"  [cyan]{cmd}[/cyan]  [dim]— {desc}[/dim]"
            for cmd, desc in matches
        ]
        lines.append(f"[dim]  (Tab 으로 첫 매치 자동완성 · Enter 로 실행)[/dim]")
        hint.update("\n".join(lines))
        hint.remove_class("hidden")

    def _hide_slash_hint(self) -> None:
        self.query_one("#slash-hint", Static).add_class("hidden")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """객관식 — Enter 로 옵션 선택."""
        if event.option_list.id != "hitl-choice":
            return
        # event.option 은 Option 인스턴스. prompt 가 표시 문자열.
        answer = str(event.option.prompt)
        self._submit_resume(answer)

    def on__inline_multi_choice_submitted(self, event: "_InlineMultiChoice.Submitted") -> None:
        """복수선택 — Enter 로 제출 (선택된 list)."""
        self._submit_resume(list(event.values))

    def on__inline_multi_choice_cancelled(self, event: "_InlineMultiChoice.Cancelled") -> None:
        """복수선택 — Esc 로 취소 (우선순위 바인딩으로 여기로 먼저 온다)."""
        self.action_cancel_hitl()

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
        elif head in ("/tool", "/tools", "/details"):
            self.action_cmd_tool_details()
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
        self._enter_normal_mode()

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
            "  [cyan]/tool[/cyan]     최근 tool 호출의 inputs/outputs 모달         — [yellow]F3[/yellow]\n"
            "  [cyan]/help[/cyan]     이 도움말                                    — F1\n"
            "  [cyan]/quit[/cyan]     종료                                         — Ctrl+C\n\n"
            "[bold]HITL 인터랙션 (입력창이 자동 전환)[/bold]\n"
            "  객관식   : ↑↓ 로 이동 · [yellow]Enter[/yellow] 로 선택 · Esc 로 취소\n"
            "  복수선택 : ↑↓ 로 이동 · [yellow]Space[/yellow] 로 체크 토글 · Enter 로 제출\n"
            "  주관식   : 답변 입력 후 Enter · Esc 로 취소\n\n"
            "[bold]슬래시 팔레트[/bold]\n"
            "  입력창에 [cyan]/[/cyan] 입력하면 명령 목록 힌트가 바로 위에 노출됨\n"
            "  [cyan]Tab[/cyan] 키로 첫 매치를 자동완성, [cyan]Enter[/cyan] 로 실행\n\n"
            "[bold]HITL 트리거 (MockLLM 기준)[/bold]\n"
            "  복수선택: 여러, 복수, 해당, 체크, 모두\n"
            "  객관식  : 추천, 고를, 고르, 골라, 선택지, 옵션\n"
            "  주관식  : 설명해, 알려줘, 명확, 모호, 구체적"
        )
        self._write_system(help_text)

    def action_cmd_tool_details(self) -> None:
        """F3 또는 /tool — 실제 tool 호출 (name 이 'tool:' 로 시작하는 span) 만 모달 표시.

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
        self.run_worker(self._run_chat_worker(text), exclusive=True, thread=True)

    def _submit_resume(self, answer: Any) -> None:
        # 사용자 응답을 대화에 한 줄 추가 (list 면 합쳐서)
        if isinstance(answer, (list, tuple)):
            display = ", ".join(str(a) for a in answer) if answer else "(선택 없음)"
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
        self._render_recent_tool_calls()
        self._write_assistant(assistant)
        self._update_status()
        if interrupt_payload:
            # 바로 인라인 HITL 모드로 전환 (팝업 없음)
            self._enter_hitl(interrupt_payload)
        else:
            # 일반 대화로 복귀
            self._enter_normal_mode()

    def _on_turn_error(self, e: Exception) -> None:
        self._write_error(f"{type(e).__name__}: {e}")
        self._update_status()
        # 오류 시에도 일반 모드로 복귀해 다음 입력을 받을 수 있게
        if self._hitl_mode is not None:
            self._enter_normal_mode()

    # ----- 렌더링 헬퍼 -----
    def _log(self) -> RichLog:
        return self.query_one("#history", RichLog)

    def _write_banner(self) -> None:
        log = self._log()
        log.write(
            f"[bold cyan]╭─ LangGraph Chat REPL[/bold cyan] · "
            f"thread [cyan]{self.engine.thread_id}[/cyan] "
            f"[bold cyan]─────────────────────────╮[/bold cyan]"
        )
        log.write(
            "[dim]  /help 로 명령어 확인 · Ctrl+C 종료 · HITL 은 입력창이 직접 전환됩니다[/dim]"
        )
        log.write(
            "[bold cyan]╰────────────────────────────────────────────────────────╯[/bold cyan]"
        )
        log.write("")

    def _write_user(self, text: str, label: str = "you") -> None:
        self._log().write(f"[bold green]● {label}[/bold green]  {text}")

    def _write_assistant(self, text: str) -> None:
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
        """직전 턴에서 생긴 tool span 을 한 줄씩 표시 (human:answered 는 제외)."""
        spans = self.engine.tracer.spans
        if not spans:
            return
        last_root_start_idx = 0
        for i in range(len(spans) - 1, -1, -1):
            if spans[i].parent_id is None and spans[i].kind == "chain":
                last_root_start_idx = i
                break
        recent_tools = [
            s for s in spans[last_root_start_idx:]
            if s.kind == "tool" and s.name.startswith("tool:")
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
        self.query_one("#status", Static).update(f"[yellow]⏳ {msg}[/yellow]")

    def _update_status(self) -> None:
        s = self.engine.summary()
        parts = [
            f"thread [cyan]{self.engine.thread_id}[/cyan]",
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
