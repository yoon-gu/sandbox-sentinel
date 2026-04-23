"""
LangGraph 기반 터미널 챗봇 REPL — prompt_toolkit 단독 구현 (Textual 불필요).

원본 출처:
    - langgraph (StateGraph / MemorySaver / interrupt / Command): https://github.com/langchain-ai/langgraph (MIT)
    - 터미널 UI: https://github.com/prompt-toolkit/python-prompt-toolkit (BSD-3-Clause)

라이선스: MIT (langgraph)
생성: Code Conversion Agent

개념
----
003-langgraph-chat-repl 과 동일한 UX (인라인 HITL, 슬래시 팔레트, Tool 상세 등) 을
**prompt_toolkit 만으로** 재구현한 버전. prompt_toolkit 은 ipython 의 필수 전이
의존이라 폐쇄망 스택에 textual 이 없어도 대부분 설치되어 있음.

주요 기능
--------
  1) 멀티턴 대화 — MemorySaver + thread_id, 매 턴 상태바가 통계 갱신
  2) 인라인 HITL (팝업 모달 없음) — 입력창이 그 자리에서 상황별 위젯으로 전환
       · choice       → 화살표 + Enter 선택
       · multi_choice → 화살표 + Space 토글 + Enter 제출
       · input        → 자유 텍스트 입력 후 Enter
     Esc 로 어느 HITL 모드든 취소
  3) 슬래시 팔레트 — '/' 입력 시 명령 목록 인라인 힌트 + Tab 자동완성
  4) Tool 상세 — Ctrl+O 또는 /tool 로 tool 호출의 inputs/outputs 를 히스토리에 펼침
  5) 트레이스 — /trace 또는 Ctrl+T 로 self-contained HTML 저장
  6) 외부 네트워크 / 새 서버 프로세스 / 포트 오픈 0

사용 예시는 examples/basic_usage.py 참고.
"""

# ===== 1. Imports =====
from __future__ import annotations

import asyncio
import datetime as _dt
import functools
import json
import os
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from langgraph.types import Command  # noqa: F401  (re-export)

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import (
    ConditionalContainer,
    HSplit,
    Layout,
    Window,
)
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension as D
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import TextArea


# ===== 2. Tracer (001/003 에서 이식) =====

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
    """메모리에 span 누적 + self-contained HTML 내보내기."""

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


# ===== 3. ChatEngine =====

class ChatEngine:
    """LangGraph 그래프를 실행하고 HITL 상태를 관리하는 UI-독립적 엔진."""

    def __init__(self, graph: Any, llm: Any, tracer: Optional[Tracer] = None,
                 thread_id: Optional[str] = None) -> None:
        self.graph = graph
        self.llm = llm
        self.tracer = tracer if tracer is not None else Tracer()
        if getattr(self.llm, "tracer", None) is None:
            try:
                self.llm.tracer = self.tracer
            except Exception:
                pass
        self.thread_id: str = thread_id or uuid.uuid4().hex[:8]
        self.pending_interrupt: Optional[dict] = None

    def chat(self, user_message: str) -> tuple[str, Optional[dict]]:
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
        if self.pending_interrupt is None:
            raise RuntimeError("해소할 인터럽트가 없습니다.")
        with self.tracer.span(
            f"resume: {str(answer)[:24]}",
            kind="chain",
            inputs={"answer": answer, "ask": self.pending_interrupt},
            metadata={"thread_id": self.thread_id},
        ) as s:
            result = self.graph.invoke(
                Command(resume=answer),
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
        state = self.graph.get_state({"configurable": {"thread_id": self.thread_id}})
        return list(state.values.get("messages", []))

    def reset(self, new_thread_id: Optional[str] = None) -> str:
        self.thread_id = new_thread_id or uuid.uuid4().hex[:8]
        self.pending_interrupt = None
        return self.thread_id

    def summary(self) -> dict:
        return self.tracer.summary()

    @staticmethod
    def _extract(result: Any) -> tuple[str, Optional[dict]]:
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


# ===== 4. 슬래시 팔레트 정의 =====

_SLASH_PALETTE: tuple[tuple[str, str], ...] = (
    ("/new", "새 thread 로 리셋 (Ctrl+N)"),
    ("/trace", "트레이스 HTML 저장 (Ctrl+T)"),
    ("/history", "대화 이력 다시 출력"),
    ("/tool", "Tool 호출 상세를 히스토리에 펼침 (Ctrl+O)"),
    ("/help", "도움말 (F1)"),
    ("/quit", "종료 (Ctrl+C)"),
)


# ===== 5. REPL App =====

class ReplApp:
    """prompt_toolkit 풀스크린 TUI — 인라인 HITL + 슬래시 팔레트.

    레이아웃 (세로):
      · 히스토리 TextArea (read-only, scrollable)
      · 상태바 (1줄, 통계)
      · HITL 배너 (조건부)
      · 슬래시 힌트 (조건부, '/' 로 시작 시)
      · Main Input (조건부, 일반 모드에서만)
      · HITL Input (조건부, input HITL 에서만)
      · HITL Choice 리스트 (조건부)
      · HITL Multi 리스트 (조건부)
    """

    def __init__(self, engine: ChatEngine) -> None:
        self.engine = engine
        # HITL 상태
        self.hitl_mode: Optional[str] = None  # None | "choice" | "multi" | "input"
        self.hitl_question: str = ""
        self.hitl_options: list[str] = []
        self.choice_cursor: int = 0
        self.multi_cursor: int = 0
        self.multi_selected: list[bool] = []
        # 기타 상태
        self.busy: bool = False
        self.slash_hint_visible: bool = False
        # 위젯/컨트롤 (build 에서 채움)
        self.app: Optional[Application] = None
        self.main_input: Optional[TextArea] = None
        self.hitl_input: Optional[TextArea] = None
        self.history_area: Optional[TextArea] = None
        self.choice_ctrl: Optional[FormattedTextControl] = None
        self.multi_ctrl: Optional[FormattedTextControl] = None

    # ---------- 렌더링 (FormattedTextControl 콜백) ----------
    def _render_status(self) -> FormattedText:
        s = self.engine.summary()
        parts: list[tuple[str, str]] = [
            ("class:status", f"  thread "),
            ("class:thread", self.engine.thread_id),
            ("class:status", f"  ·  LLM {s['llm_calls']}회"),
            ("class:status",
             f"  ·  tokens in/out {s['tokens_in']}/{s['tokens_out']}"),
            ("class:status", f"  ·  latency {s['root_latency_ms']:.0f} ms"),
        ]
        if self.busy:
            parts.insert(0, ("class:busy", "  ⏳ 처리중"))
        if self.engine.pending_interrupt is not None:
            parts.append(("class:hitl-waiting", "  ·  HITL 대기"))
        return FormattedText(parts)

    def _render_banner(self) -> FormattedText:
        if self.hitl_mode is None:
            return FormattedText([])
        label = {
            "choice": "객관식",
            "multi": "복수선택",
            "input": "주관식",
        }.get(self.hitl_mode, "HITL")
        hints = {
            "choice": "↑↓ 이동 · Enter 선택 · Esc 취소",
            "multi": "↑↓ 이동 · Space 체크 · Enter 제출 · Esc 취소",
            "input": "Enter 제출 · Esc 취소",
        }.get(self.hitl_mode, "")
        return FormattedText([
            ("class:banner", f"  🤚 {label}  "),
            ("class:banner.q", self.hitl_question),
            ("", "\n"),
            ("class:banner.hint", f"  {hints}"),
        ])

    def _render_slash_hint(self) -> FormattedText:
        if not self.slash_hint_visible:
            return FormattedText([])
        text = self.main_input.buffer.text if self.main_input else ""
        lower = text.lower()
        matches = [(cmd, desc) for cmd, desc in _SLASH_PALETTE if cmd.startswith(lower)]
        parts: list[tuple[str, str]] = []
        if not matches:
            parts.append(
                ("class:slash.hint.none",
                 "  매칭되는 명령 없음. 사용 가능 명령: ")
            )
            for cmd, _ in _SLASH_PALETTE:
                parts.append(("class:slash.cmd", cmd))
                parts.append(("", "  "))
            return FormattedText(parts)
        for i, (cmd, desc) in enumerate(matches):
            if i == 0:
                # 첫 매치 강조 (Tab 으로 채워질 항목)
                parts.append(("class:slash.first", f"  ▸ {cmd}"))
            else:
                parts.append(("class:slash.cmd", f"    {cmd}"))
            parts.append(("class:slash.desc", f"  — {desc}"))
            parts.append(("", "\n"))
        parts.append(("class:slash.footer",
                      "  (Tab 으로 첫 매치 자동완성 · Enter 로 실행)"))
        return FormattedText(parts)

    def _render_choice(self) -> FormattedText:
        parts: list[tuple[str, str]] = []
        for i, opt in enumerate(self.hitl_options or ["(옵션 없음)"]):
            if i == self.choice_cursor:
                parts.append(("class:list.cursor", f"  ▸ {opt}\n"))
            else:
                parts.append(("class:list.item", f"    {opt}\n"))
        return FormattedText(parts)

    def _render_multi(self) -> FormattedText:
        parts: list[tuple[str, str]] = []
        for i, opt in enumerate(self.hitl_options or ["(옵션 없음)"]):
            check = "[x]" if (i < len(self.multi_selected) and self.multi_selected[i]) else "[ ]"
            marker = "▸" if i == self.multi_cursor else " "
            style = "class:list.cursor" if i == self.multi_cursor else "class:list.item"
            parts.append((style, f"  {marker} {check} {opt}\n"))
        return FormattedText(parts)

    # ---------- 히스토리 조작 ----------
    def _append_history(self, line: str) -> None:
        if self.history_area is None:
            return
        new = self.history_area.text + line + "\n"
        # read_only=True 인 버퍼에도 프로그래매틱 업데이트는 허용 (bypass_readonly=True).
        # cursor_position 을 끝으로 → Window 가 자동으로 마지막 줄로 스크롤.
        self.history_area.buffer.set_document(
            Document(text=new, cursor_position=len(new)),
            bypass_readonly=True,
        )

    def _write_user(self, text: str, label: str = "you") -> None:
        self._append_history(f"● {label}   {text}")

    def _write_assistant(self, text: str) -> None:
        lines = (text or "").splitlines() or [""]
        self._append_history(f"● bot    {lines[0]}")
        for ln in lines[1:]:
            self._append_history(f"          {ln}")

    def _write_tool(self, text: str) -> None:
        self._append_history(f"  ⚙ tool   {text}")

    def _write_system(self, text: str) -> None:
        # 멀티라인 지원 (여러 줄이면 각 줄에 prefix)
        lines = (text or "").splitlines() or [""]
        self._append_history(f"● system {lines[0]}")
        for ln in lines[1:]:
            self._append_history(f"         {ln}")

    def _write_error(self, text: str) -> None:
        self._append_history(f"● error  {text}")

    def _write_banner(self) -> None:
        # 박스 그리기 문자는 단일 폭. 한글(2 폭)을 섞으면 정렬이 깨지므로 영문으로 통일.
        # 전체 폭을 고정해두고 Python 에서 패딩을 계산해 각 줄 길이를 맞춘다.
        W = 64
        tid = self.engine.thread_id
        title = f" LangGraph Chat REPL · thread {tid} "
        top = "╭" + title + "─" * (W - 2 - len(title)) + "╮"
        mid_text = "  /help for commands · Ctrl+C to quit · HITL is inline"
        mid = "│" + mid_text + " " * (W - 2 - len(mid_text)) + "│"
        bot = "╰" + "─" * (W - 2) + "╯"
        self._append_history(top)
        self._append_history(mid)
        self._append_history(bot)

    def _render_recent_tool_calls(self) -> None:
        """직전 턴의 tool:* span 을 히스토리에 한 줄씩 표시."""
        spans = self.engine.tracer.spans
        if not spans:
            return
        last_root_idx = 0
        for i in range(len(spans) - 1, -1, -1):
            if spans[i].parent_id is None and spans[i].kind == "chain":
                last_root_idx = i
                break
        for s in spans[last_root_idx:]:
            if s.kind == "tool" and s.name.startswith("tool:"):
                out = s.outputs
                if isinstance(out, dict):
                    summary = out.get("content") or out.get("answer") or str(out)
                else:
                    summary = str(out) if out is not None else ""
                self._write_tool(f"{s.name} → {str(summary)[:100]}")

    # ---------- 모드 전환 ----------
    def _enter_normal(self) -> None:
        self.hitl_mode = None
        self.hitl_question = ""
        self.hitl_options = []
        self.multi_selected = []
        if self.main_input is not None and self.app is not None:
            self.app.layout.focus(self.main_input)
        if self.app is not None:
            self.app.invalidate()

    def _enter_hitl(self, payload: dict) -> None:
        qtype = payload.get("type", "input")
        mapping = {"choice": "choice", "multi_choice": "multi", "input": "input"}
        self.hitl_mode = mapping.get(qtype, "input")
        self.hitl_question = str(payload.get("question") or "")
        self.hitl_options = list(payload.get("options") or [])
        self.choice_cursor = 0
        self.multi_cursor = 0
        self.multi_selected = [False] * len(self.hitl_options)
        # 슬래시 힌트는 HITL 에선 숨김
        self.slash_hint_visible = False
        if self.app is None:
            return
        if self.hitl_mode == "input":
            if self.hitl_input is not None:
                self.hitl_input.buffer.text = ""
                self.app.layout.focus(self.hitl_input)
        elif self.hitl_mode == "choice":
            if self.choice_ctrl is not None:
                self.app.layout.focus(self.choice_ctrl)
        elif self.hitl_mode == "multi":
            if self.multi_ctrl is not None:
                self.app.layout.focus(self.multi_ctrl)
        self.app.invalidate()

    def _cancel_hitl(self) -> None:
        if self.hitl_mode is None:
            return
        self._write_system(
            "HITL 취소됨. 그래프는 여전히 응답 대기 중입니다. "
            "같은 트리거 문구를 다시 보내거나 /new 로 리셋하세요."
        )
        self._enter_normal()

    # ---------- 백그라운드 실행 (그래프 호출) ----------
    async def _run_chat(self, text: str) -> None:
        self.busy = True
        if self.app is not None:
            self.app.invalidate()
        try:
            loop = asyncio.get_event_loop()
            assistant, interrupt_payload = await loop.run_in_executor(
                None, functools.partial(self.engine.chat, text),
            )
            self._on_turn_result(assistant, interrupt_payload)
        except Exception as e:
            self._write_error(f"{type(e).__name__}: {e}")
        finally:
            self.busy = False
            if self.app is not None:
                self.app.invalidate()

    async def _run_resume(self, answer: Any) -> None:
        self.busy = True
        if self.app is not None:
            self.app.invalidate()
        try:
            loop = asyncio.get_event_loop()
            assistant, interrupt_payload = await loop.run_in_executor(
                None, functools.partial(self.engine.resume, answer),
            )
            self._on_turn_result(assistant, interrupt_payload)
        except Exception as e:
            self._write_error(f"{type(e).__name__}: {e}")
        finally:
            self.busy = False
            if self.app is not None:
                self.app.invalidate()

    def _on_turn_result(self, assistant: str, interrupt_payload: Optional[dict]) -> None:
        self._render_recent_tool_calls()
        self._write_assistant(assistant)
        if interrupt_payload:
            self._enter_hitl(interrupt_payload)
        else:
            self._enter_normal()

    # ---------- 제출 헬퍼 ----------
    def _submit_chat(self, text: str) -> None:
        self._write_user(text)
        asyncio.ensure_future(self._run_chat(text))

    def _submit_resume(self, answer: Any) -> None:
        if isinstance(answer, (list, tuple)):
            display = ", ".join(str(a) for a in answer) if answer else "(선택 없음)"
        else:
            display = str(answer)
        self._write_user(display, label="answer")
        asyncio.ensure_future(self._run_resume(answer))

    # ---------- 슬래시 명령 ----------
    def _handle_command(self, cmd: str) -> None:
        head = cmd.split()[0].lower()
        if head == "/quit":
            if self.app is not None:
                self.app.exit()
        elif head == "/new":
            self._cmd_new()
        elif head == "/trace":
            self._cmd_trace()
        elif head == "/history":
            self._cmd_history()
        elif head in ("/tool", "/tools", "/details"):
            self._cmd_tool_details()
        elif head == "/help":
            self._cmd_help()
        else:
            self._write_system(f"알 수 없는 명령: {cmd} — /help 로 명령어 확인")

    def _cmd_new(self) -> None:
        new_id = self.engine.reset()
        # 히스토리 초기화 (bypass_readonly)
        if self.history_area is not None:
            self.history_area.buffer.set_document(
                Document(text="", cursor_position=0), bypass_readonly=True,
            )
        self._write_banner()
        self._write_system(f"새 thread 로 리셋됨 → {new_id}")
        self._enter_normal()

    def _cmd_trace(self) -> None:
        ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"trace_{self.engine.thread_id}_{ts}.html"
        self.engine.tracer.save_html(
            filename,
            title=f"Trace — thread {self.engine.thread_id} @ {ts}",
        )
        abs_path = os.path.abspath(filename)
        spans = len(self.engine.tracer.spans)
        self._write_system(
            f"✓ 트레이스 저장됨 · {spans} spans\n"
            f"  경로: {abs_path}\n"
            f"  브라우저로 열면 span 트리 확인 가능 (self-contained HTML)"
        )

    def _cmd_history(self) -> None:
        msgs = self.engine.history()
        if not msgs:
            self._write_system("(대화 이력 없음)")
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

    def _cmd_tool_details(self) -> None:
        """Ctrl+O 또는 /tool — 현재 Tracer 의 tool:* span 을 펼쳐서 히스토리에 쓴다.

        prompt_toolkit 에선 모달보다는 히스토리에 직접 쓰는 게 일관된 UX.
        """
        tool_spans = [
            s for s in self.engine.tracer.spans
            if s.kind == "tool" and s.name.startswith("tool:")
        ]
        if not tool_spans:
            self._write_system("(tool 호출 기록 없음 — 'calculator' 같은 키워드를 먼저 시도해보세요)")
            return
        self._write_system(f"⚙ Tool 호출 상세 — {len(tool_spans)} 건:")
        for i, s in enumerate(tool_spans, 1):
            latency = (s.end - s.start) * 1000.0 if s.end is not None else 0.0
            self._write_system(f"#{i}  {s.name}   ({latency:.1f} ms)")
            self._write_system(f"  ■ inputs:  {self._fmt_json(s.inputs)}")
            self._write_system(f"  ■ outputs: {self._fmt_json(s.outputs)}")
            if s.metadata:
                self._write_system(f"  ■ metadata: {self._fmt_json(s.metadata)}")
            if s.error:
                self._write_system(f"  ■ error: {s.error}")

    @staticmethod
    def _fmt_json(v: Any) -> str:
        if v is None:
            return "(없음)"
        try:
            return json.dumps(v, ensure_ascii=False, default=str)
        except Exception:
            return str(v)

    def _cmd_help(self) -> None:
        text = (
            "슬래시 명령 · 단축키\n"
            "  /new      새 thread 로 리셋 (맥락 끊기, Tracer 유지)  — Ctrl+N\n"
            "  /trace    현재 트레이스를 HTML 파일로 저장            — Ctrl+T\n"
            "  /history  현재 thread 의 대화 이력 다시 출력\n"
            "  /tool     최근 tool 호출 상세를 히스토리에 펼침        — Ctrl+O\n"
            "  /help     이 도움말                                   — F1\n"
            "  /quit     종료                                        — Ctrl+C\n"
            "\n"
            "슬래시 팔레트\n"
            "  입력창에 / 입력하면 명령 목록이 힌트로 노출. Tab 으로 첫 매치 자동완성.\n"
            "\n"
            "HITL 인터랙션 (입력창이 자동 전환)\n"
            "  객관식   : ↑↓ 로 이동 · Enter 로 선택 · Esc 로 취소\n"
            "  복수선택 : ↑↓ 로 이동 · Space 로 토글 · Enter 로 제출 · Esc 로 취소\n"
            "  주관식   : 답변 입력 후 Enter · Esc 로 취소\n"
            "\n"
            "HITL 트리거 (MockLLM 기준)\n"
            "  복수선택: 여러, 복수, 해당, 체크, 모두\n"
            "  객관식  : 추천, 고를, 고르, 골라, 선택지, 옵션\n"
            "  주관식  : 설명해, 알려줘, 명확, 모호, 구체적"
        )
        self._write_system(text)

    # ---------- 슬래시 힌트 업데이트 ----------
    def _on_main_text_changed(self, _buffer: Buffer) -> None:
        if self.hitl_mode is not None:
            self.slash_hint_visible = False
            return
        text = self.main_input.buffer.text if self.main_input else ""
        self.slash_hint_visible = text.startswith("/")
        if self.app is not None:
            self.app.invalidate()

    # ---------- 레이아웃 + 바인딩 구성 ----------
    def build(self) -> Application:
        # 히스토리 (읽기 전용, 스크롤 가능)
        self.history_area = TextArea(
            text="",
            read_only=True,
            scrollbar=True,
            wrap_lines=True,
            focusable=False,
            style="class:history",
        )

        # 상태바
        status_win = Window(
            content=FormattedTextControl(text=self._render_status, focusable=False),
            height=1,
            style="class:status-bar",
        )

        # HITL 배너 (조건부)
        banner_win = ConditionalContainer(
            content=Window(
                content=FormattedTextControl(text=self._render_banner, focusable=False),
                height=2,
                style="class:banner-bar",
            ),
            filter=Condition(lambda: self.hitl_mode is not None),
        )

        # 슬래시 힌트 (조건부)
        slash_win = ConditionalContainer(
            content=Window(
                content=FormattedTextControl(text=self._render_slash_hint, focusable=False),
                height=D(min=1, max=8),
                style="class:slash-bar",
            ),
            filter=Condition(lambda: self.slash_hint_visible),
        )

        # Main input (일반 모드 전용)
        def _accept_main(buf: Buffer) -> bool:
            value = buf.text.strip()
            if not value:
                return False   # falsy → Buffer.reset() (빈 상태 유지)
            if value.startswith("/"):
                self._handle_command(value)
            else:
                self._submit_chat(value)
            # prompt_toolkit Buffer.validate_and_handle 규약:
            #   accept_handler 가 truthy 반환 → keep_text=True, 버퍼 그대로
            #   falsy 반환 → self.reset() 으로 버퍼 비움
            # 우리는 submit 후 입력창을 비워야 하므로 False 를 반환
            return False

        self.main_input = TextArea(
            height=1,
            prompt="> ",
            multiline=False,
            accept_handler=_accept_main,
            style="class:input",
        )
        self.main_input.buffer.on_text_changed += self._on_main_text_changed
        main_input_cont = ConditionalContainer(
            content=self.main_input,
            filter=Condition(lambda: self.hitl_mode is None),
        )

        # HITL input (주관식)
        def _accept_hitl_input(buf: Buffer) -> bool:
            value = buf.text.strip()
            if not value:
                return False
            self._submit_resume(value)
            # Falsy → Buffer.reset() 으로 입력창 비움 (위 _accept_main 주석 참고)
            return False

        self.hitl_input = TextArea(
            height=1,
            prompt="답변> ",
            multiline=False,
            accept_handler=_accept_hitl_input,
            style="class:input",
        )
        hitl_input_cont = ConditionalContainer(
            content=self.hitl_input,
            filter=Condition(lambda: self.hitl_mode == "input"),
        )

        # HITL choice — 전용 key bindings 가 붙은 focusable 컨트롤
        choice_kb = KeyBindings()

        @choice_kb.add("up")
        def _(event):
            if self.choice_cursor > 0:
                self.choice_cursor -= 1

        @choice_kb.add("down")
        def _(event):
            if self.choice_cursor < max(0, len(self.hitl_options) - 1):
                self.choice_cursor += 1

        @choice_kb.add("enter")
        def _(event):
            if self.hitl_options:
                answer = self.hitl_options[self.choice_cursor]
                self._submit_resume(answer)

        @choice_kb.add("escape")
        def _(event):
            self._cancel_hitl()

        self.choice_ctrl = FormattedTextControl(
            text=self._render_choice,
            focusable=True,
            key_bindings=choice_kb,
            show_cursor=False,
        )
        choice_win = ConditionalContainer(
            content=Window(
                content=self.choice_ctrl,
                height=D(min=2, max=10),
                style="class:list",
            ),
            filter=Condition(lambda: self.hitl_mode == "choice"),
        )

        # HITL multi — 전용 key bindings
        multi_kb = KeyBindings()

        @multi_kb.add("up")
        def _(event):
            if self.multi_cursor > 0:
                self.multi_cursor -= 1

        @multi_kb.add("down")
        def _(event):
            if self.multi_cursor < max(0, len(self.hitl_options) - 1):
                self.multi_cursor += 1

        @multi_kb.add("space")
        def _(event):
            if self.multi_selected and self.multi_cursor < len(self.multi_selected):
                self.multi_selected[self.multi_cursor] = not self.multi_selected[self.multi_cursor]

        @multi_kb.add("enter")
        def _(event):
            selected = [
                self.hitl_options[i]
                for i, v in enumerate(self.multi_selected) if v
            ]
            self._submit_resume(selected)

        @multi_kb.add("escape")
        def _(event):
            self._cancel_hitl()

        self.multi_ctrl = FormattedTextControl(
            text=self._render_multi,
            focusable=True,
            key_bindings=multi_kb,
            show_cursor=False,
        )
        multi_win = ConditionalContainer(
            content=Window(
                content=self.multi_ctrl,
                height=D(min=2, max=10),
                style="class:list",
            ),
            filter=Condition(lambda: self.hitl_mode == "multi"),
        )

        # 전역 key bindings
        global_kb = KeyBindings()

        @global_kb.add("c-c")
        def _(event):
            event.app.exit()

        @global_kb.add("c-n")
        def _(event):
            self._cmd_new()

        @global_kb.add("c-t")
        def _(event):
            self._cmd_trace()

        @global_kb.add("f1")
        def _(event):
            self._cmd_help()

        @global_kb.add("c-o")
        def _(event):
            self._cmd_tool_details()

        # Tab — main_input 포커스 + '/' 로 시작할 때만 자동완성 (filter)
        @global_kb.add(
            "tab",
            filter=Condition(lambda: (
                self.hitl_mode is None
                and self.main_input is not None
                and self.main_input.buffer.text.startswith("/")
            )),
        )
        def _(event):
            text = self.main_input.buffer.text
            matches = [cmd for cmd, _ in _SLASH_PALETTE if cmd.startswith(text.lower())]
            if matches:
                self.main_input.buffer.document = Document(
                    text=matches[0], cursor_position=len(matches[0]),
                )

        # Esc — HITL input 모드에서 hitl_input 에 포커스가 있을 때 취소
        # (choice/multi 모달은 각자 자체 kb 에서 escape 를 처리)
        @global_kb.add(
            "escape",
            filter=Condition(lambda: self.hitl_mode == "input"),
        )
        def _(event):
            self._cancel_hitl()

        # Layout 조립
        root = HSplit([
            self.history_area,
            status_win,
            banner_win,
            slash_win,
            main_input_cont,
            hitl_input_cont,
            choice_win,
            multi_win,
        ])

        style = Style([
            ("history", "bg:default"),
            ("status-bar", "bg:#202530 #a0a8b8"),
            ("status", "bg:#202530 #a0a8b8"),
            ("thread", "bg:#202530 #5fd7ff bold"),
            ("busy", "bg:#202530 #ffd75f bold"),
            ("hitl-waiting", "bg:#202530 #ffaf00 bold"),
            ("banner-bar", "bg:#3a2a00 #ffd75f"),
            ("banner", "bg:#3a2a00 #ffd75f bold"),
            ("banner.q", "bg:#3a2a00 #ffffff"),
            ("banner.hint", "bg:#3a2a00 #a8a878 italic"),
            ("slash-bar", "bg:#202838 #d0d8f8"),
            ("slash.first", "bg:#202838 #afffaf bold"),
            ("slash.cmd", "bg:#202838 #87d7ff"),
            ("slash.desc", "bg:#202838 #a0a0a0 italic"),
            ("slash.hint.none", "bg:#202838 #ff8787"),
            ("slash.footer", "bg:#202838 #808080 italic"),
            ("list", "bg:default"),
            ("list.cursor", "bg:#2a3a5a #87ffd7 bold"),
            ("list.item", "bg:default"),
            ("input", ""),
        ])

        self.app = Application(
            layout=Layout(root, focused_element=self.main_input),
            key_bindings=global_kb,
            style=style,
            full_screen=True,
            mouse_support=False,
        )

        # 초기 화면
        self._write_banner()
        self._enter_normal()
        return self.app


# ===== 6. Public entry — launch() =====

def launch(graph: Any, llm: Any, tracer: Optional[Tracer] = None,
           thread_id: Optional[str] = None) -> None:
    """사용자 그래프 + LLM 을 REPL 로 기동.

    사용 예:
        from repl import launch
        launch(graph=my_compiled_graph, llm=MyLLMAdapter())
    """
    engine = ChatEngine(graph=graph, llm=llm, tracer=tracer, thread_id=thread_id)
    app_obj = ReplApp(engine)
    app = app_obj.build()
    app.run()


# ===== 7. HTML 템플릿 (self-contained trace viewer) =====

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
  function esc(s) { return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
  function fmt(v) { if (v === null || v === undefined) return '(없음)'; try { return JSON.stringify(v, null, 2); } catch (e) { return String(v); } }
  function render(parentId) {
    const kids = byParent[parentId] || [];
    return kids.map(s => {
      const meta = [];
      meta.push((s.latency_ms || 0).toFixed(1) + ' ms');
      if (s.kind === 'llm') meta.push('in:' + (s.tokens_in || 0) + ' / out:' + (s.tokens_out || 0));
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
          (s.metadata && Object.keys(s.metadata).length ? '\n\n■ metadata\n' + esc(fmt(s.metadata)) : '') +
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


if __name__ == "__main__":
    print(
        "이 파일은 라이브러리입니다. 사용 예시는 examples/basic_usage.py 를 참고하세요:\n"
        "  python examples/basic_usage.py"
    )
