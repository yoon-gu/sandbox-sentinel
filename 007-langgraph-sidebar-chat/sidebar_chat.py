"""
LangGraph 기반 Jupyter 우측 사이드바 챗봇 (노트북 변수 인식)

원본 출처:
    - langgraph (StateGraph / MemorySaver): https://github.com/langchain-ai/langgraph (MIT)
    - 사이드바 패널: jupyter `sidecar` 위젯 (BSD-3-Clause) — 선택 의존성, 없으면 셀 인라인으로 폴백
라이선스: MIT (langgraph)
생성: Code Conversion Agent

이 변환물이 하는 일
  1) JupyterLab '우측 영역'에 떠 있는 챗봇 패널을 띄운다 (sidecar 위젯, 순수 파이썬).
  2) 챗봇이 **현재 노트북에 살아있는 변수**(get_ipython().user_ns)를 들여다보고 맥락으로 활용한다.
  3) 로컬 LLM(사내 vLLM/Ollama/llama.cpp 등 OpenAI 호환 서버)에 **표준 라이브러리 urllib 로만** 연결한다.
     (폐쇄망 정책상 openai SDK / requests / httpx 사용 불가)
  4) 외부 인터넷 호출·바이너리 영속화 없음. 대화 기록은 self-contained HTML 로만 내보낸다.

폐쇄망 친화 설계
  - 모듈 최상단은 표준 라이브러리만 import 한다.
  - langgraph 는 `SidebarChat` 생성 시점에 lazy import → langgraph 가 없는 환경에서도
    변수 인식(inspect_namespace) · LLM 어댑터 · HTML 렌더 유틸을 단독으로 쓸 수 있다.
  - ipywidgets / sidecar / IPython 은 UI 를 실제로 띄울 때만 import 한다 (없으면 친절히 폴백).

빠른 사용 (노트북 셀):
    from sidebar_chat import open_sidebar_chat
    bot = open_sidebar_chat()          # MockLLM 으로 우측 패널 오픈 (서버 없이 동작)
    # 실제 사내 LLM 연결:
    # from sidebar_chat import LocalOpenAILLM, open_sidebar_chat
    # llm = LocalOpenAILLM(base_url="http://localhost:8000/v1", model="qwen3")
    # bot = open_sidebar_chat(llm=llm)
"""

# ===== 1. Imports (표준 라이브러리만) =====
import inspect
import json
import logging
import urllib.error
import urllib.request
import uuid
from typing import Annotated, Any, Optional, TypedDict

logger = logging.getLogger(__name__)


# ===== 2. 노트북 변수 인식 (핵심 기능) =====
#
# "사이드바 챗봇이 떠있는 노트북의 변수를 볼 수 있는가?" 라는 요구사항의 본체.
# 노트북 변수는 프론트엔드(브라우저)가 아니라 '커널'의 네임스페이스에 살아있다.
# 이 코드는 커널 안에서 돌기 때문에 get_ipython().user_ns 로 그 변수들을 직접 읽을 수 있다.

# IPython 이 사용자 네임스페이스에 자동으로 끼워넣는 내부 이름들 — 변수 목록에서 제외한다.
_IPYTHON_INTERNAL_NAMES = {
    "In", "Out", "get_ipython", "exit", "quit", "open",
}

# 변수 목록에서 제외할 자체 클래스/객체 타입 이름 (챗봇 객체 자신이 변수로 잡히는 것 방지).
_INTERNAL_TYPE_NAMES = {"SidebarChat", "MockLLM", "LocalOpenAILLM", "Sidecar"}


def _get_user_ns() -> dict:
    """현재 IPython 커널의 사용자 네임스페이스(dict)를 반환. 노트북 밖이면 빈 dict."""
    try:
        from IPython import get_ipython
    except ImportError:
        return {}
    ip = get_ipython()
    # 일반 파이썬 인터프리터(노트북 밖)에서는 None 이 반환된다.
    return dict(ip.user_ns) if ip is not None else {}


def _is_data_like(name: str, value: Any) -> bool:
    """LLM 에게 보여줄 '데이터성' 변수인지 판정. 모듈/함수/클래스/내부객체는 제외한다."""
    if name.startswith("_"):
        return False
    if name in _IPYTHON_INTERNAL_NAMES:
        return False
    if inspect.ismodule(value) or inspect.isroutine(value) or inspect.isclass(value):
        return False
    if type(value).__name__ in _INTERNAL_TYPE_NAMES:
        return False
    # 챗봇/LLM 어댑터 인스턴스 (dict invoke 계약 표식 보유) 제외
    if getattr(value, "_dict_invoke_contract", False):
        return False
    return True


def _summarize_value(value: Any) -> str:
    """변수 하나의 타입/모양 요약 문자열. 값 자체는 넣지 않는다 (메타데이터만)."""
    tname = type(value).__name__
    try:
        # pandas DataFrame: shape + 앞쪽 컬럼명 (columns 보유로 가장 먼저 식별)
        if hasattr(value, "shape") and hasattr(value, "columns"):
            cols = [str(c) for c in list(value.columns)[:8]]
            more = "…" if len(getattr(value, "columns", [])) > 8 else ""
            return f"DataFrame shape={tuple(value.shape)} cols={cols}{more}"
        # pandas Series: dtype + index 보유, columns 없음 (ndarray 보다 먼저 검사)
        if hasattr(value, "dtype") and hasattr(value, "index"):
            return f"Series len={len(value)} dtype={value.dtype}"
        # numpy ndarray 등: shape + dtype
        if hasattr(value, "shape") and hasattr(value, "dtype"):
            return f"{tname} shape={tuple(value.shape)} dtype={value.dtype}"
        # 컨테이너: 길이
        if isinstance(value, (list, tuple, set, dict)):
            return f"{tname} len={len(value)}"
        # 문자열: 길이
        if isinstance(value, str):
            return f"str len={len(value)}"
        # 스칼라: 값 그대로 (작고 민감하지 않음)
        if isinstance(value, (int, float, bool)):
            return f"{tname} = {value!r}"
    except Exception:
        # 요약 중 예외가 나면 타입명만 (사용자 정의 객체의 property 부작용 방지)
        return tname
    return tname


def _preview_value(value: Any, max_chars: int = 120) -> str:
    """변수 값의 짧은 미리보기. include_values=True 일 때만 사용. 길면 잘라낸다."""
    try:
        s = repr(value)
    except Exception:
        return "(repr 실패)"
    s = " ".join(s.split())  # 개행/연속 공백 정리
    return s if len(s) <= max_chars else s[:max_chars] + "…"


def inspect_namespace(namespace: Optional[dict] = None, *,
                      include_values: bool = False,
                      max_vars: int = 40,
                      max_preview_chars: int = 120) -> list:
    """현재 노트북 네임스페이스에서 데이터성 변수만 골라 요약 리스트로 반환.

    Args:
        namespace: 검사할 dict. None 이면 get_ipython().user_ns (노트북 전역 변수).
        include_values: False(기본)면 이름/타입/요약만. True 면 짧은 값 미리보기 포함.
            → 금융 폐쇄망에서 고객/민감 데이터가 LLM 프롬프트로 새어 나가지 않도록
              **기본값은 보수적으로 값 미포함**. 값까지 보여주려면 명시적으로 True.
        max_vars: 너무 많은 변수가 프롬프트를 넘치게 하지 않도록 상한.

    Returns:
        [{"name", "type", "summary", "preview"(opt)} ...] — 이름 알파벳 순.
    """
    if namespace is None:
        namespace = _get_user_ns()

    items = []
    for name, value in namespace.items():
        if not _is_data_like(name, value):
            continue
        entry = {
            "name": name,
            "type": type(value).__name__,
            "summary": _summarize_value(value),
        }
        if include_values:
            entry["preview"] = _preview_value(value, max_preview_chars)
        items.append(entry)

    items.sort(key=lambda d: d["name"])
    return items[:max_vars]


def render_var_summary_text(varlist: list, *, include_values: bool = False) -> str:
    """변수 요약 리스트를 LLM system 프롬프트에 넣을 평문 블록으로 변환."""
    if not varlist:
        return "[현재 노트북 변수] 사용자가 정의한 변수가 아직 없습니다."

    lines = [f"[현재 노트북 변수 {len(varlist)}개]"]
    for v in varlist:
        line = f"- {v['name']}: {v['summary']}"
        if include_values and v.get("preview"):
            line += f"  | 값: {v['preview']}"
        lines.append(line)
    return "\n".join(lines)


def render_var_panel_html(varlist: list, *, include_values: bool = False) -> str:
    """사이드바 상단 '변수 패널'용 self-contained HTML 표.

    사용자가 '챗봇이 지금 무엇을 보고 있는지' 한눈에 확인할 수 있도록 한다 (투명성).
    """
    if not varlist:
        body = ('<div style="color:#888;font-size:11px;padding:6px">'
                '아직 노트북에 사용자 정의 변수가 없습니다.</div>')
    else:
        rows = []
        for v in varlist:
            name = _html_escape(str(v["name"]))
            summary = _html_escape(str(v["summary"]))
            preview = ""
            if include_values and v.get("preview"):
                preview = (f'<div style="color:#9ca3af;font-size:10px;'
                           f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis">'
                           f'{_html_escape(str(v["preview"]))}</div>')
            rows.append(
                f'<tr><td style="padding:3px 8px;font-family:monospace;'
                f'color:#1e40af;white-space:nowrap">{name}</td>'
                f'<td style="padding:3px 8px;color:#444;font-size:11px">'
                f'{summary}{preview}</td></tr>'
            )
        body = ('<table style="width:100%;border-collapse:collapse;font-size:12px">'
                + "".join(rows) + "</table>")
    return (
        '<div style="border:1px solid #e5e5e5;border-radius:6px;'
        'max-height:160px;overflow:auto;background:#fafafa">'
        f'{body}</div>'
    )


# ===== 3. LLM 어댑터 =====
#
# 공통 계약: invoke(messages: list[dict]) -> dict
#   - messages: [{"role": "system"|"user"|"assistant", "content": "..."}, ...]
#   - 반환:     {"role": "assistant", "content": "..."}
# 이 계약만 지키면 어떤 LLM 도 SidebarChat 에 끼울 수 있다.

_DEFAULT_SYSTEM_PROMPT = (
    "당신은 Jupyter 노트북 사이드바에서 동작하는 한국어 데이터 분석 어시스턴트입니다.\n"
    "아래에 현재 노트북에 살아있는 변수 목록이 주어집니다. 사용자의 질문에 답할 때 이 변수들을 적극 참고하세요.\n"
    "- 답변은 간결하고 친절하게, 초급 분석가도 이해할 수 있게 설명합니다.\n"
    "- 변수 정보가 부족하면 추측하지 말고 사용자에게 되물어 확인합니다.\n"
    "- 코드 예시는 실행 가능한 형태로 제시합니다."
)


class MockLLM:
    """외부 서버 없이 동작하는 데모용 LLM. 변수 인식이 되는지 바로 확인할 수 있도록,
    system 프롬프트에 들어온 노트북 변수 목록을 그대로 요약해 응답에 실어 보낸다."""

    # SidebarChat 가 이 객체를 '변수'로 오인하지 않도록 + 어댑터 자동감지 표식
    _dict_invoke_contract = True

    def __init__(self, name: str = "mock-sidebar-llm"):
        self.name = name

    def invoke(self, messages: list) -> dict:
        system = next((m.get("content", "") for m in messages
                       if m.get("role") == "system"), "")
        last_user = next((m.get("content", "") for m in reversed(messages)
                          if m.get("role") == "user"), "")

        # system 프롬프트에서 변수 블록만 추출해 "내가 본 변수" 로 되돌려준다.
        var_block = ""
        marker = "[현재 노트북 변수"
        if marker in system:
            var_block = system[system.index(marker):].strip()

        seen = "현재 보이는 변수가 없습니다." if not var_block else var_block
        content = (
            f"(MockLLM 응답 · 실제 모델 아님) 질문을 받았습니다: '{last_user[:80]}'\n\n"
            f"제가 지금 노트북에서 보고 있는 정보는 다음과 같아요:\n{seen}\n\n"
            f"실제 사내 LLM(LocalOpenAILLM)을 연결하면 이 변수들을 활용한 의미 있는 답변이 나옵니다."
        )
        return {"role": "assistant", "content": content}


class LocalOpenAILLM:
    """로컬 OpenAI 호환 추론 서버에 **표준 라이브러리 urllib 로만** 붙는 어댑터.

    폐쇄망 정책상 openai SDK / requests / httpx 가 모두 차단되어 있으므로,
    urllib.request 만으로 `/v1/chat/completions` 엔드포인트를 호출한다.
    외부 인터넷이 아니라 localhost(사내) 추론 서버를 향한 호출이며,
    사내 프록시가 localhost 를 가로채지 않도록 프록시를 우회한다.

    호환 서버 예: vLLM, Ollama(`/v1`), llama.cpp server, Text Generation Inference 등.
    """

    _dict_invoke_contract = True

    def __init__(self, base_url: str = "http://localhost:8000/v1",
                 model: str = "local-model",
                 api_key: Optional[str] = None,
                 temperature: float = 0.2,
                 timeout: float = 60.0,
                 max_tokens: Optional[int] = None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.timeout = timeout
        self.max_tokens = max_tokens
        # localhost 는 사내 프록시를 우회 — 빈 ProxyHandler 로 프록시 환경변수 무시
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def invoke(self, messages: list) -> dict:
        url = self.base_url + "/chat/completions"
        body = {
            "model": self.model,
            "messages": self._to_openai_messages(messages),
            "temperature": self.temperature,
        }
        if self.max_tokens is not None:
            body["max_tokens"] = self.max_tokens

        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise RuntimeError(
                f"LLM 서버가 HTTP {e.code} 를 반환했습니다 ({url}).\n응답: {detail}"
            ) from e
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"LLM 서버에 연결할 수 없습니다 ({url}).\n"
                f"- 로컬 추론 서버가 떠 있는지, base_url/포트가 맞는지 확인하세요.\n"
                f"- 원인: {e.reason}"
            ) from e

        try:
            content = payload["choices"][0]["message"].get("content", "")
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(
                f"LLM 응답 형식이 OpenAI 호환이 아닙니다. 원본: {str(payload)[:300]}"
            ) from e
        return {"role": "assistant", "content": content}

    @staticmethod
    def _to_openai_messages(messages: list) -> list:
        """내부 dict 메시지를 OpenAI chat 형식으로 정리 (role 검증 + content 문자열화)."""
        allowed = {"system", "user", "assistant"}
        out = []
        for m in messages:
            role = m.get("role", "user")
            if role not in allowed:
                role = "user"  # 그 외 역할은 user 로 합쳐 단순화
            out.append({"role": role, "content": str(m.get("content", ""))})
        return out


# ===== 4. LangGraph 그래프 (inspect → chat) =====
#
# 단순한 2-노드 선형 그래프로 단일 책임 원칙을 지킨다 (로컬 LLM 친화: 프롬프트/상태 최소화).
#   inspect 노드: 현재 노트북 변수를 읽어 system 컨텍스트 문자열을 만든다.
#   chat   노드: (system + 변수 컨텍스트) + 대화 이력을 LLM 에 넘겨 응답을 받는다.
# 멀티턴 메모리는 MemorySaver + thread_id 가 담당한다.

def _append_messages(left: Optional[list], right: Any) -> list:
    """TypedDict 리듀서: 새 메시지(들)를 기존 리스트 뒤에 이어붙인다."""
    if right is None:
        return left or []
    if not isinstance(right, list):
        right = [right]
    return (left or []) + right


class ChatState(TypedDict, total=False):
    messages: Annotated[list, _append_messages]   # user/assistant 대화 이력 (system 은 미저장)
    var_context: str                              # 이번 턴의 변수 요약 (매 턴 새로 계산, 미저장)


def _inspect_node(state: ChatState, config) -> dict:
    """현재 노트북 변수를 읽어 system 에 넣을 컨텍스트 문자열을 만든다."""
    cfg = (config or {}).get("configurable", {})
    namespace = cfg.get("namespace")            # None 이면 라이브 user_ns 사용
    include_values = cfg.get("include_values", False)
    max_vars = cfg.get("max_vars", 40)

    varlist = inspect_namespace(namespace, include_values=include_values, max_vars=max_vars)
    return {"var_context": render_var_summary_text(varlist, include_values=include_values)}


def _chat_node(state: ChatState, config) -> dict:
    """system(기본 프롬프트 + 변수 컨텍스트) + 대화 이력을 LLM 에 넘겨 응답을 받는다."""
    cfg = (config or {}).get("configurable", {})
    llm = cfg.get("llm")
    if llm is None:
        raise RuntimeError("config['configurable']['llm'] 에 LLM 어댑터를 주입하세요.")
    base_prompt = cfg.get("system_prompt", _DEFAULT_SYSTEM_PROMPT)

    var_context = state.get("var_context", "")
    system_msg = {"role": "system", "content": f"{base_prompt}\n\n{var_context}"}

    # 대화 이력(user/assistant)만 추려 system 뒤에 붙인다. system 은 매 턴 새로 만들어
    # 항상 '최신' 변수 상태를 반영하고, 이력에는 누적시키지 않는다.
    convo = [m for m in state.get("messages", []) if isinstance(m, dict)]
    assistant = llm.invoke([system_msg] + convo)
    return {"messages": [assistant]}


def _build_graph():
    """langgraph 그래프를 컴파일한다. langgraph 는 여기서 lazy import (없는 환경 보호)."""
    try:
        from langgraph.graph import StateGraph, START, END
        from langgraph.checkpoint.memory import MemorySaver
    except ImportError as e:
        raise RuntimeError(
            "SidebarChat 는 langgraph 가 필요합니다. 폐쇄망 기본 스택에는 langgraph 1.0.10 이 "
            "포함됩니다. 변수 인식·LLM 어댑터·HTML 렌더만 쓰려면 langgraph 없이도 "
            "inspect_namespace / LocalOpenAILLM / render_conversation_html 을 직접 호출하세요."
        ) from e

    g = StateGraph(ChatState)
    g.add_node("inspect", _inspect_node)
    g.add_node("chat", _chat_node)
    g.add_edge(START, "inspect")
    g.add_edge("inspect", "chat")
    g.add_edge("chat", END)
    return g.compile(checkpointer=MemorySaver())


# ===== 5. SidebarChat 고수준 API =====

class SidebarChat:
    """노트북 우측 사이드바에 떠 있는, 변수를 인식하는 멀티턴 챗봇.

    - `open()`     : sidecar 위젯으로 JupyterLab 우측 영역에 패널을 띄운다.
    - `show_inline()`: sidecar 없이 현재 셀 output 에 동일한 UI 를 표시 (폴백).
    - `chat(text)` : 프로그래매틱 한 턴 대화 (UI 없이도 사용 가능).
    - 변수 인식    : 매 턴마다 현재 노트북 변수를 읽어 LLM 의 system 컨텍스트로 넣는다.
    """

    def __init__(self, llm: Optional[Any] = None, *,
                 thread_id: Optional[str] = None,
                 include_values: bool = False,
                 max_vars: int = 40,
                 system_prompt: Optional[str] = None,
                 namespace: Optional[dict] = None,
                 title: str = "🤖 노트북 어시스턴트"):
        self.llm = llm if llm is not None else MockLLM()
        self.app = _build_graph()           # 여기서 langgraph lazy import
        self.thread_id = thread_id or uuid.uuid4().hex[:8]
        self.include_values = include_values
        self.max_vars = max_vars
        self.system_prompt = system_prompt or _DEFAULT_SYSTEM_PROMPT
        # namespace=None 이면 매 턴 라이브 user_ns 를 읽는다. 테스트 시 dict 주입 가능.
        self._namespace = namespace
        self.title = title
        self._sidecar = None

    # ----- 핵심 동작 -----
    def _config(self) -> dict:
        return {"configurable": {
            "thread_id": self.thread_id,
            "llm": self.llm,
            "include_values": self.include_values,
            "max_vars": self.max_vars,
            "system_prompt": self.system_prompt,
            "namespace": self._namespace,
        }}

    def chat(self, user_message: str) -> str:
        """한 턴 대화. 같은 thread_id 라 맥락이 이어진다. 최종 assistant 텍스트 반환."""
        result = self.app.invoke(
            {"messages": [{"role": "user", "content": user_message}]},
            config=self._config(),
        )
        return self._last_assistant(result)

    @staticmethod
    def _last_assistant(result: Any) -> str:
        if not isinstance(result, dict):
            return ""
        for m in reversed(result.get("messages", []) or []):
            if isinstance(m, dict) and m.get("role") == "assistant":
                return m.get("content", "")
        return ""

    def history(self) -> list:
        """현재 thread 의 대화 이력 (checkpointer 저장본)."""
        state = self.app.get_state({"configurable": {"thread_id": self.thread_id}})
        return list(state.values.get("messages", []))

    def reset(self, new_thread_id: Optional[str] = None) -> str:
        """새 thread 로 대화를 초기화."""
        self.thread_id = new_thread_id or uuid.uuid4().hex[:8]
        return self.thread_id

    def current_variables(self) -> list:
        """챗봇이 지금 보고 있는 노트북 변수 목록 (UI 변수 패널과 동일)."""
        return inspect_namespace(self._namespace,
                                 include_values=self.include_values,
                                 max_vars=self.max_vars)

    # ----- 영속화 (HTML only) -----
    def export_html(self, path: Optional[str] = None) -> str:
        """대화 이력을 self-contained HTML 로 내보낸다. path 가 있으면 파일로 저장."""
        html = render_conversation_html(self.history(), thread_id=self.thread_id)
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)
            return path
        return html

    # ----- UI -----
    def open(self, anchor: str = "right"):
        """JupyterLab 우측 영역에 sidecar 패널로 챗봇 UI 를 띄운다.

        sidecar 가 없으면 현재 셀에 인라인으로 폴백한다 (기능은 동일).
        anchor: 'right'(우측 사이드바) 외에 'split-right','split-left' 등 sidecar 값 지원.
        """
        widget = self._build_widget()
        try:
            from sidecar import Sidecar
        except ImportError:
            logger.warning("sidecar 미설치 → 셀 인라인으로 표시합니다. "
                           "우측 패널을 쓰려면 사내 미러에서 sidecar 를 설치하세요.")
            return self._display_inline(widget)

        try:
            sc = Sidecar(title=self.title, anchor=anchor)
        except Exception:
            # 일부 구버전 sidecar 는 anchor='right' 를 모르거나 anchor 인자가 없다 → 기본 위치
            sc = Sidecar(title=self.title)
        self._sidecar = sc
        from IPython.display import display
        with sc:
            display(widget)
        return sc

    def show_inline(self):
        """sidecar 없이 현재 셀 output 안에 동일한 챗봇 UI 를 표시 (폴백/대안)."""
        return self._display_inline(self._build_widget())

    @staticmethod
    def _display_inline(widget):
        from IPython.display import display
        display(widget)
        return widget

    def _build_widget(self):
        """ipywidgets 기반 챗봇 UI(VBox) 를 구성해 반환."""
        try:
            import ipywidgets as W
            from IPython.display import HTML, display, clear_output
        except ImportError as e:
            raise RuntimeError(
                "UI 는 ipywidgets 가 필요합니다. 없으면 chat()/export_html() 을 "
                "프로그래매틱하게 사용하세요."
            ) from e

        mode = "MockLLM (데모)" if isinstance(self.llm, MockLLM) else type(self.llm).__name__
        header = W.HTML(value=(
            f"<div style='font-size:12px;color:#444'>"
            f"<b>{_html_escape(self.title)}</b><br>"
            f"<span style='color:#888;font-size:11px'>"
            f"LLM: {_html_escape(mode)} · thread <code>{_html_escape(self.thread_id)}</code> · "
            f"값표시 {'ON' if self.include_values else 'OFF'}</span></div>"
        ))

        var_panel = W.HTML(value=render_var_panel_html(
            self.current_variables(), include_values=self.include_values))
        var_refresh = W.Button(description="변수 새로고침", icon="refresh",
                               layout=W.Layout(width="auto"))
        var_section = W.VBox([
            W.HTML(value="<div style='font-size:11px;color:#666;margin-top:4px'>"
                         "📊 챗봇이 보고 있는 노트북 변수</div>"),
            var_panel, var_refresh,
        ])

        history_area = W.Output(layout=W.Layout(
            border="1px solid #e5e5e5", border_radius="6px",
            min_height="180px", max_height="360px", overflow="auto", padding="0",
            margin="6px 0",
        ))
        input_box = W.Text(placeholder="메시지를 입력하고 Enter 또는 '보내기'…",
                           layout=W.Layout(width="100%"))
        send_btn = W.Button(description="보내기", button_style="primary", icon="paper-plane")
        export_btn = W.Button(description="대화 내보내기(HTML)", icon="download",
                              layout=W.Layout(width="auto"))
        reset_btn = W.Button(description="새 대화", button_style="warning", icon="refresh",
                             layout=W.Layout(width="auto"))
        status = W.HTML(value="")
        export_area = W.Output()

        def _refresh_vars(_btn=None):
            var_panel.value = render_var_panel_html(
                self.current_variables(), include_values=self.include_values)

        def _render_history():
            with history_area:
                clear_output(wait=True)
                display(HTML(render_conversation_html(self.history(), self.thread_id)))

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
                _refresh_vars()       # 대화 후 변수 패널도 최신화
                status.value = ""
            except Exception as e:
                status.value = (f"<span style='color:#b91c1c;font-size:11px'>"
                                f"에러: {type(e).__name__}: {_html_escape(str(e))}</span>")
            finally:
                send_btn.disabled = False

        def _on_export(_btn=None):
            import base64 as _b64
            import datetime as _dt
            ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            filename = f"chat_{self.thread_id}_{ts}.html"
            html_src = render_conversation_html(self.history(), self.thread_id)
            data_url = ("data:text/html;base64,"
                        + _b64.b64encode(html_src.encode("utf-8")).decode("ascii"))
            with export_area:
                clear_output(wait=True)
                display(HTML(
                    f'<div style="font-size:12px;padding:8px 10px;background:#ecfdf5;'
                    f'border:1px solid #86efac;border-radius:6px;color:#065f46">'
                    f'✅ 대화 준비됨 — <a href="{data_url}" download="{_html_escape(filename)}" '
                    f'style="color:#047857;font-weight:500">💾 {_html_escape(filename)} 다운로드</a>'
                    f'</div>'))

        def _on_reset(_btn=None):
            self.reset()
            _render_history()
            _refresh_vars()
            with export_area:
                clear_output(wait=True)
            status.value = ("<span style='color:#047857;font-size:11px'>"
                            "새 대화로 초기화되었습니다.</span>")
            header.value = header.value  # thread 라벨은 다음 렌더에서 갱신

        send_btn.on_click(_on_send)
        try:
            input_box.on_submit(_on_send)   # ipywidgets 8 에서 deprecated 이나 동작 (버튼 fallback 존재)
        except Exception:
            pass
        var_refresh.on_click(_refresh_vars)
        export_btn.on_click(_on_export)
        reset_btn.on_click(_on_reset)

        _render_history()

        return W.VBox([
            header,
            var_section,
            history_area,
            input_box,
            W.HBox([send_btn, export_btn, reset_btn]),
            status,
            export_area,
        ], layout=W.Layout(width="100%", padding="8px"))


def open_sidebar_chat(llm: Optional[Any] = None, *, anchor: str = "right", **kwargs):
    """한 줄로 사이드바 챗봇을 띄우는 헬퍼. SidebarChat 를 만들고 open() 까지 호출."""
    bot = SidebarChat(llm=llm, **kwargs)
    bot.open(anchor=anchor)
    return bot


# ===== 6. HTML 렌더 (self-contained, 외부 리소스 참조 없음) =====

def _html_escape(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


_CONVO_HTML_TEMPLATE = """<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<title>대화 기록</title>
<style>
  body { margin:0; padding:12px; background:#fafafa; }
  .chat { font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Apple SD Gothic Neo","Malgun Gothic",sans-serif;
          max-width:720px; margin:0 auto; background:#fff; border:1px solid #e5e5e5;
          border-radius:8px; padding:12px; }
  .chat h3 { font-size:13px; color:#666; margin:0 0 10px 0; font-weight:500; }
  .msg { display:flex; gap:8px; margin:8px 0; align-items:flex-start; }
  .msg.u { flex-direction:row-reverse; }
  .role { font-size:10px; padding:2px 6px; border-radius:3px; background:#eee; color:#444;
          text-transform:uppercase; height:fit-content; }
  .msg.u .role { background:#dbeafe; color:#1e40af; }
  .msg.a .role { background:#eef2ff; color:#4338ca; }
  .bubble { padding:8px 12px; border-radius:10px; background:#f3f4f6; max-width:520px;
            white-space:pre-wrap; word-break:break-word; font-size:13px; line-height:1.5; }
  .msg.u .bubble { background:#dbeafe; }
  .empty { color:#888; font-size:12px; padding:20px; text-align:center; }
</style></head><body><div class="chat">
<h3>대화 기록 (thread: {{TID}})</h3>
{{BODY}}
</div></body></html>"""


def render_conversation_html(messages: list, thread_id: str = "") -> str:
    """대화 이력을 채팅 풍선 스타일의 self-contained HTML 로 렌더 (업무망 반출용)."""
    bubbles = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role", "?")
        if role not in ("user", "assistant"):
            continue
        cls = "u" if role == "user" else "a"
        content = _html_escape(str(m.get("content", "")))
        bubbles.append(
            f'<div class="msg {cls}"><span class="role">{_html_escape(role)}</span>'
            f'<div class="bubble">{content}</div></div>'
        )
    body = "\n".join(bubbles) if bubbles else '<div class="empty">대화 기록이 비어 있습니다.</div>'
    return (_CONVO_HTML_TEMPLATE
            .replace("{{TID}}", _html_escape(thread_id))
            .replace("{{BODY}}", body))


# ===== 7. Example Usage =====

if __name__ == "__main__":
    # 노트북 밖(일반 파이썬)에서도 핵심 기능을 확인할 수 있는 헤드리스 데모.
    # 실제 노트북에서는 `from sidebar_chat import open_sidebar_chat; bot = open_sidebar_chat()` 한 줄이면 된다.

    # 1) 변수 인식 — 노트북 user_ns 대신 샘플 dict 를 직접 넣어 시연
    sample_ns = {
        "x": 42,
        "ratios": [0.1, 0.2, 0.7],
        "config": {"lr": 0.01, "epochs": 10},
        "title": "분기 리포트",
        "_hidden": "표시 안 됨",          # _ 접두어 → 제외
        "json": json,                     # 모듈 → 제외
    }
    varlist = inspect_namespace(sample_ns, include_values=True)
    print("=== 인식된 노트북 변수 ===")
    print(render_var_summary_text(varlist, include_values=True))

    # 2) MockLLM — 변수 컨텍스트를 system 으로 받으면 그대로 인식해 응답
    print("\n=== MockLLM 응답 (변수 인식 확인) ===")
    sys_msg = {"role": "system",
               "content": _DEFAULT_SYSTEM_PROMPT + "\n\n" +
               render_var_summary_text(varlist)}
    user_msg = {"role": "user", "content": "지금 어떤 변수들이 보여?"}
    print(MockLLM().invoke([sys_msg, user_msg])["content"])

    # 3) langgraph 가 있으면 SidebarChat 전체 흐름까지 시연
    try:
        bot = SidebarChat(namespace=sample_ns, include_values=True)
        print("\n=== SidebarChat 멀티턴 (MockLLM) ===")
        print("BOT:", bot.chat("내 변수들 요약해줘"))
        print("BOT:", bot.chat("방금 질문 기억나?"))
        path = bot.export_html("conversation.html")
        print(f"\n대화 기록을 '{path}' 에 저장했습니다. 브라우저로 열어보세요.")
    except RuntimeError as e:
        print(f"\n[langgraph 미설치] SidebarChat 데모는 건너뜁니다: {e}")
