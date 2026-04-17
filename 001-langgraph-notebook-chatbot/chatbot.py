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

        if tool_reply:
            text = (
                f"[턴 {turn}] 도구 실행 결과를 확인했습니다. → {tool_reply} "
                f"(원 질문: '{last_user[:60]}')"
            )
        else:
            text = (
                f"[턴 {turn}] 입력: '{last_user[:80]}'. "
                f"대화에는 지금까지 {len(messages)}개 메시지가 누적되어 있습니다."
            )

        tokens_in = sum(len(str(m.get("content", "")).split()) for m in messages)
        tokens_out = len(text.split())
        # latency 시뮬레이션 — 실제 LLM처럼 출력 길이에 비례
        time.sleep(tokens_out * self.per_token_ms / 1000.0)

        return {"role": "assistant", "content": text}, tokens_in, tokens_out


# ===== 4. LangGraph 챗봇 그래프 =====

def _append_messages(left: Optional[list], right: Any) -> list:
    """TypedDict 리듀서: 새 메시지(들)를 기존 리스트 뒤에 이어붙인다."""
    if right is None:
        return left or []
    if not isinstance(right, list):
        right = [right]
    return (left or []) + right


class ChatState(TypedDict):
    messages: Annotated[list, _append_messages]


# 주의: langgraph는 노드의 두 번째 파라미터를 무(無)타입 혹은 RunnableConfig로 기대합니다.
# `config: dict` 처럼 일반 타입을 붙이면 config가 주입되지 않으니 그대로 둡니다.
def _chat_node(state: ChatState, config) -> dict:
    """한 턴의 처리: (1) 간단한 tool 감지 → 있으면 실행 후 LLM 재호출, (2) 없으면 바로 LLM."""
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
    return {"messages": new_messages}


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
    g.add_edge(START, "chat")
    g.add_edge("chat", END)
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
        self.llm = llm if llm is not None else MockLLM(tracer=self.tracer)
        # 외부에서 주입한 LLM이 tracer를 들고 있지 않다면 붙여준다
        if getattr(self.llm, "tracer", None) is None:
            try:
                self.llm.tracer = self.tracer
            except Exception:
                pass
        self.app = _build_graph()
        self.thread_id = thread_id or uuid.uuid4().hex[:8]

    def chat(self, user_message: str) -> str:
        """한 턴의 대화. 최종 assistant 응답 문자열을 반환."""
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
            # 마지막 assistant 메시지 추출 (tool 메시지가 중간에 있을 수 있음)
            assistant = next(
                (m["content"] for m in reversed(result["messages"])
                 if m.get("role") == "assistant"),
                "",
            )
            self.tracer.finish(s, outputs={"assistant": assistant})
        return assistant

    def history(self) -> list[dict]:
        """현재 thread의 대화 이력 (checkpointer에 저장된 메시지 리스트)."""
        state = self.app.get_state({"configurable": {"thread_id": self.thread_id}})
        return list(state.values.get("messages", []))

    def reset(self, new_thread_id: Optional[str] = None) -> str:
        """새 thread로 리셋. 기존 Tracer 기록은 유지 (원하면 clear_trace 호출)."""
        self.thread_id = new_thread_id or uuid.uuid4().hex[:8]
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

        반환값을 셀의 마지막 표현식으로 두면, 입력창 + 보내기/트레이스/리셋 버튼
        + 대화 풍선 + 트레이스 뷰어를 하나의 output에 렌더합니다.

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
        input_box = W.Textarea(
            placeholder="메시지를 입력하고 '보내기' 를 누르세요. (여러 줄 입력 가능)",
            layout=W.Layout(width="100%", height="70px"),
        )
        send_btn = W.Button(description="보내기", button_style="primary",
                            icon="paper-plane")
        trace_btn = W.Button(description="트레이스 보기", icon="bar-chart")
        reset_btn = W.Button(description="새 대화 (thread 리셋)",
                             button_style="warning", icon="refresh")
        status = W.HTML(value="")

        history_area = W.Output(layout=W.Layout(
            border="1px solid #e5e5e5", border_radius="6px",
            max_height="420px", overflow="auto", padding="0",
        ))
        trace_area = W.Output()

        def _render_history():
            with history_area:
                clear_output(wait=True)
                display(HTML(_render_history_html(
                    self.history(), thread_id=self.thread_id)))

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
                s = self.summary()
                status.value = (
                    f"<span style='color:#666;font-size:11px'>"
                    f"LLM 호출 {s['llm_calls']}회 · "
                    f"토큰 in/out {s['tokens_in']}/{s['tokens_out']} · "
                    f"누적 latency {s['root_latency_ms']:.0f} ms</span>"
                )
            except Exception as e:
                status.value = (
                    f"<span style='color:#b91c1c;font-size:11px'>"
                    f"에러: {type(e).__name__}: {e}</span>"
                )
            finally:
                send_btn.disabled = False

        def _on_trace(_btn):
            with trace_area:
                clear_output(wait=True)
                display(HTML(self.tracer.to_html(
                    title=f"Trace — thread {self.thread_id}")))

        def _on_reset(_btn):
            self.reset()
            tid_label.value = self._thread_label_html()
            _render_history()
            with trace_area:
                clear_output(wait=True)
            status.value = (
                "<span style='color:#047857;font-size:11px'>"
                "새 thread 로 리셋되었습니다.</span>"
            )

        send_btn.on_click(_on_send)
        trace_btn.on_click(_on_trace)
        reset_btn.on_click(_on_reset)

        _render_history()

        buttons = W.HBox([send_btn, trace_btn, reset_btn])
        return W.VBox([
            tid_label,
            history_area,
            input_box,
            buttons,
            status,
            trace_area,
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
        role = m.get("role", "?")
        content = _html_escape(str(m.get("content", "")))
        cls = {
            "user": "u",
            "assistant": "a",
            "tool": "t",
        }.get(role, "o")
        name_badge = f'<span class="role">{_html_escape(role)}</span>'
        bubbles.append(
            f'<div class="msg {cls}">{name_badge}<div class="bubble">{content}</div></div>'
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

    print("\nUSER: 앞에서 뭐라고 물어봤지?")
    print("BOT :", bot.chat("앞에서 뭐라고 물어봤지?"))

    print("\n=== 요약 ===")
    print(json.dumps(bot.summary(), ensure_ascii=False, indent=2))

    path = bot.save_trace("trace.html")
    print(f"\n트레이스가 '{path}'에 저장되었습니다. 브라우저로 열어보세요.")
