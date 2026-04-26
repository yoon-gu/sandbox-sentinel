"""
SQL Runner TUI — Textual 기반 single-file SQL 편집기 + 실행 위젯.

005 / 006 변환물 비교:
  · 005 = 노트북 + CodeMirror 인라인 (~285KB, trusted notebook 필요)
  · 006 = **터미널 TUI (Textual)** (이 파일) — 노트북/브라우저 불필요,
          ssh 친화, 에디터 자체에 SQL syntax color (Textual TextArea
          native + tree-sitter-sql)

라이선스: MIT (오리지널 wrapper) · Textual MIT
생성: Code Conversion Agent

핵심 기능
--------
  1) 좌측 entity Tree — 컬럼 선택 후 Enter → 에디터 커서 위치에 인서트
  2) 우측 TextArea — SQL syntax highlight (Textual native, tree-sitter SQL)
  3) **인라인 추천 OptionList** — 에디터 바로 아래에 항상 보이는 컨텍스트
     인식 추천 리스트. 에디터 입력에 따라 자동 갱신.
     · Tab    : 에디터 → 추천 리스트로 포커스 이동
     · ↑↓     : 추천 후보 사이 이동
     · Enter  : 선택 → 에디터 커서 위치에 인서트 + 에디터 복귀
     · Esc/Tab: 에디터로 복귀 (선택 없이)
  4) Ctrl+R / F5 → on_execute(sql) 콜백 호출, DataTable 에 결과 표시
  5) 외부 네트워크 / CDN / 바이너리 영속화 0 — 단일 .py 반입

사용 예시
--------
    from sql_tui import SQLRunnerTUI

    runner = SQLRunnerTUI.with_sqlite("./demo.db")
    runner.set_query("SELECT * FROM users LIMIT 10;")
    runner.run()      # 풀스크린 TUI 진입

또는 콜백 직접 주입:

    import pandas as pd, sqlite3
    runner = SQLRunnerTUI(on_execute=lambda sql: pd.read_sql(
        sql, sqlite3.connect("./demo.db")))
    runner.from_sqlite("./demo.db")
    runner.run()

키 바인딩
--------
    Ctrl+R / F5     ▶ 실행
    Tab             에디터 ↔ 인라인 추천 리스트 포커스 이동
    Ctrl+T          트리 포커스
    Ctrl+E          에디터 포커스
    Ctrl+L          에디터 비우기
    F1              도움말
    Ctrl+Q / Ctrl+C 종료
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any, Callable, Iterable, Mapping, Optional, Union

# ===== 타입 alias =====

ColumnSpec = Union[str, tuple, Mapping[str, Any]]


# ===== SQL 키워드 / 함수 / anchor (005 / 006 와 동일 세트) =====

_KEYWORDS = [
    "SELECT", "FROM", "WHERE", "AND", "OR", "NOT", "IN", "LIKE", "IS", "NULL",
    "JOIN", "INNER", "LEFT", "RIGHT", "FULL", "OUTER", "ON", "USING", "AS",
    "GROUP", "ORDER", "BY", "HAVING", "LIMIT", "OFFSET",
    "DISTINCT", "ALL", "UNION", "EXCEPT", "INTERSECT",
    "INSERT", "UPDATE", "DELETE", "INTO", "VALUES", "SET",
    "CREATE", "ALTER", "DROP", "TABLE", "INDEX", "VIEW", "WITH", "RECURSIVE",
    "CASE", "WHEN", "THEN", "ELSE", "END",
    "ASC", "DESC", "BETWEEN", "EXISTS",
    "TRUE", "FALSE",
]
_FUNCTIONS = [
    "COUNT", "SUM", "AVG", "MIN", "MAX",
    "COALESCE", "NULLIF", "IFNULL",
    "UPPER", "LOWER", "LENGTH", "SUBSTR", "TRIM", "REPLACE",
    "ROUND", "FLOOR", "CEIL", "ABS",
    "DATE", "DATETIME", "STRFTIME", "JULIANDAY",
    "CAST",
]
_ANCHORS = {
    "SELECT", "FROM", "WHERE", "JOIN", "ON", "AND", "OR",
    "GROUP", "ORDER", "HAVING", "LIMIT", "BY",
    "INSERT", "UPDATE", "DELETE", "SET", "INTO", "VALUES",
    "INNER", "LEFT", "RIGHT", "FULL",
    "UNION", "EXCEPT", "INTERSECT",
    "AS", "WITH",
}


# ===== 타입 단축 (005 와 동일 매핑) =====

def _short_type(t: str) -> str:
    """SQL 타입명을 짧은 이모지로 단축."""
    if not t:
        return ""
    u = t.upper()
    if "INT" in u or "SERIAL" in u:
        return "🔢"
    if any(k in u for k in ("REAL", "FLOAT", "DOUBLE", "NUMERIC",
                             "DECIMAL", "MONEY")):
        return "📊"
    if any(k in u for k in ("CHAR", "TEXT", "STRING", "CLOB")):
        return "📝"
    if any(k in u for k in ("TIMESTAMP", "DATE", "TIME")):
        return "📅"
    if "BOOL" in u:
        return "✓"
    if any(k in u for k in ("BLOB", "BINARY", "BYTEA")):
        return "📦"
    if "JSON" in u:
        return "🧬"
    if "UUID" in u:
        return "🆔"
    return u[:1] or "?"


# ===== 컨텍스트 감지 + 추천 (005 와 동일 정책) =====

def detect_context(text: str) -> str:
    """직전 anchor 키워드로 추천 종류를 결정.

    weak anchor (`AS` / `WITH` / `VALUES`) 는 콤마를 지나친 뒤에는
    건너뛰고 더 깊은 clause anchor(SELECT 등)를 찾는다.
    """
    s = re.sub(r"--[^\n]*", " ", text)
    s = re.sub(r"/\*.*?\*/", " ", s, flags=re.DOTALL)
    s = re.sub(r"'[^']*'", " ", s)
    s = re.sub(r'"[^"]*"', " ", s)
    tokens = s.split()
    if not tokens:
        return "start"
    WEAK = {"AS", "WITH", "VALUES"}
    seen_comma = False
    last = None
    last_idx = -1
    for i in range(len(tokens) - 1, -1, -1):
        tok = tokens[i]
        if "," in tok:
            seen_comma = True
        tu = tok.upper()
        if tu in _ANCHORS:
            if tu in WEAK and seen_comma:
                continue
            last = tu
            last_idx = i
            break
    if last is None:
        return "start"
    if last in ("GROUP", "ORDER") and last_idx + 1 < len(tokens):
        if tokens[last_idx + 1].upper() == "BY":
            last = last + "_BY"
    if last == "BY" and last_idx > 0:
        prev = tokens[last_idx - 1].upper()
        if prev in ("GROUP", "ORDER"):
            last = prev + "_BY"
    MAP = {
        "SELECT": "columns_or_star",
        "FROM": "tables", "JOIN": "tables", "INTO": "tables", "UPDATE": "tables",
        "INNER": "join_continue", "LEFT": "join_continue",
        "RIGHT": "join_continue", "FULL": "join_continue",
        "ON": "columns", "WHERE": "columns", "AND": "columns", "OR": "columns",
        "GROUP_BY": "columns", "ORDER_BY": "columns", "HAVING": "columns",
        "SET": "columns",
        "LIMIT": "number", "DELETE": "from_keyword",
        "VALUES": "any", "AS": "any", "WITH": "any",
    }
    return MAP.get(last, "general")


# ===== 테이블 alias 추출 (005 와 동일 정책) =====

_NOT_ALIAS = {
    "WHERE", "ON", "GROUP", "ORDER", "HAVING", "LIMIT", "JOIN",
    "INNER", "LEFT", "RIGHT", "FULL", "OUTER", "CROSS", "UNION",
    "EXCEPT", "INTERSECT", "AS", "USING", "SET", "VALUES",
}
_CLAUSE_END_RE = re.compile(
    r"\b(?:WHERE|GROUP|ORDER|HAVING|LIMIT|JOIN|INNER|LEFT|RIGHT|FULL"
    r"|OUTER|CROSS|UNION|EXCEPT|INTERSECT|ON|USING)\b",
    re.IGNORECASE,
)
_TABLE_REF_RE = re.compile(
    r"^\s*(\w+(?:\.\w+)?)\s*(?:(?:AS\s+)?(\w+))?\s*$",
    re.IGNORECASE,
)
_FROM_RE = re.compile(r"\bFROM\b", re.IGNORECASE)
_JOIN_RE = re.compile(
    r"\bJOIN\s+(\w+(?:\.\w+)?)(?:\s+(?:AS\s+)?(\w+))?",
    re.IGNORECASE,
)


def extract_aliases(text: str, tables: Mapping[str, list]) -> dict:
    """``FROM <t> [AS] <alias>`` / ``JOIN <t> [AS] <alias>`` 스캔.

    지원: 콤마 join (`FROM x, y`), schema-qualified (`public.t AS o`),
    본명 자체 매핑 (`orders → orders` / `o → orders`).
    """
    s = re.sub(r"--[^\n]*", " ", text)
    s = re.sub(r"/\*.*?\*/", " ", s, flags=re.DOTALL)
    s = re.sub(r"'[^']*'", " ", s)
    s = re.sub(r'"[^"]*"', " ", s)
    aliases: dict = {}

    def _register(tname_full: str, alias: Optional[str]) -> None:
        tname = tname_full.split(".")[-1]
        if tname not in tables:
            return
        aliases[tname] = tname
        if alias and alias.upper() not in _NOT_ALIAS:
            aliases[alias] = tname

    for m in _FROM_RE.finditer(s):
        rest = s[m.end():]
        end_m = _CLAUSE_END_RE.search(rest)
        from_clause = rest[:end_m.start()] if end_m else rest
        for part in from_clause.split(","):
            part = part.strip().rstrip(";").strip()
            if not part:
                continue
            tm = _TABLE_REF_RE.match(part)
            if not tm:
                continue
            _register(tm.group(1), tm.group(2))
    for m in _JOIN_RE.finditer(s):
        _register(m.group(1), m.group(2))
    return aliases


def get_suggestions(text: str, tables: Mapping[str, list],
                     full_text: Optional[str] = None) -> list:
    """현재 컨텍스트에 맞는 추천 후보 리스트.

    Args:
        text: cursor 까지의 텍스트 (컨텍스트 감지 + 마지막 부분 단어).
        tables: 스키마 매핑.
        full_text: 전체 SQL. alias 추출에 사용. None 이면 ``text``.
            SELECT 절(FROM 보다 앞)에서도 alias 가 잡히려면 전체 텍스트
            전달 필요.
    """
    ctx = detect_context(text)
    m = re.search(r"([\w_.]+)$", text)
    last_word = m.group(1) if m else ""
    last_lower = last_word.lower()

    # table_or_alias. qualifier 우선
    if "." in last_word:
        dot_idx = last_word.index(".")
        qual = last_word[:dot_idx]
        col_prefix = last_word[dot_idx + 1:].lower()
        aliases = extract_aliases(
            full_text if full_text is not None else text, tables
        )
        real = aliases.get(qual)
        if real and real in tables:
            return [
                {"value": f"{qual}.{c['name']}",
                 "label": (f"{c['name']} {_short_type(c.get('type',''))}"
                           if c.get("type") else c["name"]),
                 "kind": "column",
                 "meta": c.get("type", "") or real}
                for c in tables[real]
                if c["name"].lower().startswith(col_prefix)
            ][:30]

    cands: list = []
    if ctx in ("tables", "general", "start"):
        for tname in tables.keys():
            cands.append({"value": tname, "label": tname,
                          "kind": "table", "meta": "table"})
    if ctx in ("columns", "columns_or_star", "general", "start"):
        seen: set = set()
        for tname, cols in tables.items():
            for c in cols:
                if c["name"] in seen:
                    continue
                seen.add(c["name"])
                type_str = c.get("type", "") or ""
                # 005 와 동일 — 컬럼 라벨에 짧은 타입 이모지 부착
                col_label = (f"{c['name']} {_short_type(type_str)}"
                              if type_str else c["name"])
                meta = (type_str + " · " if type_str else "") + tname
                cands.append({"value": c["name"], "label": col_label,
                              "kind": "column", "meta": meta})
    if ctx == "columns_or_star":
        cands.insert(0, {"value": "*", "label": "*",
                         "kind": "star", "meta": "all"})
    if ctx == "join_continue":
        cands.append({"value": "JOIN", "label": "JOIN",
                      "kind": "keyword", "meta": "join"})
        cands.append({"value": "OUTER JOIN", "label": "OUTER JOIN",
                      "kind": "keyword", "meta": "join"})
    if ctx == "from_keyword":
        cands.append({"value": "FROM", "label": "FROM",
                      "kind": "keyword", "meta": "kw"})

    # KEYWORDS / FUNCTIONS fallback
    seen_v = {c["value"] for c in cands}
    for kw in _KEYWORDS:
        if kw not in seen_v:
            cands.append({"value": kw, "label": kw,
                          "kind": "keyword", "meta": "kw"})
            seen_v.add(kw)
    for fn in _FUNCTIONS:
        v = fn + "("
        if v not in seen_v:
            cands.append({"value": v, "label": v,
                          "kind": "function", "meta": "fn"})
            seen_v.add(v)

    if last_lower:
        cands = [c for c in cands if last_lower in c["label"].lower()]
    return cands[:30]


# ===== 컬럼 스펙 정규화 =====

def _normalize_column(c: ColumnSpec) -> dict:
    if isinstance(c, str):
        return {"name": c, "type": "", "doc": ""}
    if isinstance(c, tuple):
        return {"name": c[0],
                "type": c[1] if len(c) > 1 else "",
                "doc": c[2] if len(c) > 2 else ""}
    if isinstance(c, Mapping):
        return {"name": str(c["name"]),
                "type": str(c.get("type", "")),
                "doc": str(c.get("doc", ""))}
    raise TypeError(f"알 수 없는 컬럼 스펙 형식: {type(c).__name__}")


# ===== Textual TUI =====
# (textual 은 lazy import — 헤드리스 단위 검증에서도 모듈 import 자체는 가능)

def _build_app(*, on_execute, tables, notes, initial_query, app_state=None):
    """SQLRunnerTUI.run() 시점에 textual 을 import 하고 App 클래스를 동적 구성.

    이 패턴은 basic_usage.py 처럼 textual 을 띄우지 않는 단위
    검증 시에도 sql_tui 모듈을 import 할 수 있게 해준다.
    """
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical
    from textual.screen import ModalScreen
    from textual.widgets import (
        Header, Footer, Tree, TextArea, Static, DataTable, OptionList,
    )
    from textual.widgets.option_list import Option
    from rich.text import Text

    # ── TextArea 는 그대로 (Tab 은 indent 기본 동작) ──
    # Ctrl+Space 만 popup trigger 로 추가. Shift+Tab 은 dedent.
    class _SqlTextArea(TextArea):
        BINDINGS = [
            Binding("ctrl+space", "trigger_popup",
                    description="자동완성", show=False, priority=True),
            Binding("shift+tab", "dedent",
                    description="들여쓰기 해제", show=False, priority=True),
        ]

        def action_trigger_popup(self) -> None:
            self.app.action_show_popup()

        def action_dedent(self) -> None:
            """현재 줄(또는 선택 범위 줄들) 의 앞 indent 제거.

            Textual TextArea 기본에는 dedent 가 없어 직접 구현.
            line[0:N] 의 공백/탭을 indent_width 만큼 제거.
            """
            sel = self.selection
            start_line = min(sel.start[0], sel.end[0])
            end_line = max(sel.start[0], sel.end[0])
            width = self.indent_width
            for line_idx in range(start_line, end_line + 1):
                try:
                    line = self.document.get_line(line_idx)
                except Exception:
                    continue
                # 앞쪽 공백/탭 N자 (최대 indent_width)
                n = 0
                while (n < min(width, len(line))
                       and line[n] in (" ", "\t")):
                    n += 1
                if n > 0:
                    self.replace(
                        "",
                        start=(line_idx, 0),
                        end=(line_idx, n),
                        maintain_selection_offset=True,
                    )

    # ── 커서 근처에 떠 있는 floating 자동완성 popup ──
    # 에디터 입력에 따라 자동 표시. Ctrl+Space 로도 수동 호출.
    # Tab/Enter 로 선택 · Esc/Tab 으로 닫기. 글자 입력 시 에디터로 forwarding.
    class _CursorPopup(OptionList):
        DEFAULT_CSS = """
        _CursorPopup {
            layer: popup;
            width: 38;
            max-height: 9;
            background: $panel;
            border: round $primary;
            display: none;
        }
        _CursorPopup.visible {
            display: block;
        }
        """
        BINDINGS = [
            Binding("escape", "dismiss_popup", show=False),
            Binding("tab",    "accept_current", show=False),
        ]

        def action_dismiss_popup(self) -> None:
            self.app._hide_popup()

        def action_accept_current(self) -> None:
            if self.option_count and self.highlighted is not None:
                self.action_select()   # OptionList 내장 — OptionSelected 발화

        def _on_key(self, event) -> None:
            # 글자 입력 / Backspace 는 에디터로 forwarding 후 popup 새로 채움.
            # Up/Down/Enter/Tab/Esc/Home/End 등은 popup 키 바인딩 처리.
            ch = event.character
            forward = False
            if ch and len(ch) == 1 and ch.isprintable() and event.key not in (
                    "up", "down", "left", "right", "enter", "escape", "tab",
                    "home", "end", "pageup", "pagedown",
            ):
                forward = True
            elif event.key == "backspace":
                forward = True
            if forward:
                ed = self.app.query_one("#editor", _SqlTextArea)
                ed.focus()
                if event.key == "backspace":
                    ed.action_delete_left()
                else:
                    ed.insert(ch)
                event.prevent_default()
                event.stop()

    # ── 도움말 모달 ──
    class _HelpScreen(ModalScreen[None]):
        BINDINGS = [Binding("escape,q,f1", "dismiss", "Close")]
        DEFAULT_CSS = """
        _HelpScreen { align: center middle; }
        _HelpScreen > Vertical {
            width: 76; height: auto; padding: 1 2;
            border: thick $primary; background: $surface;
        }
        """

        def compose(self) -> ComposeResult:
            with Vertical():
                yield Static(Text.from_markup(
                    "[b]단축키[/]\n\n"
                    "  [yellow]Tab[/]      에디터 → 인라인 추천 리스트로 포커스 이동\n"
                    "  [yellow]Ctrl+R[/]   ▶ 실행 (현재 SQL 을 on_execute 콜백에 전달)\n"
                    "  [yellow]F5[/]       ▶ 실행 (Ctrl+R 과 동일)\n"
                    "  [yellow]Ctrl+T[/]   트리 포커스 (테이블/컬럼 선택)\n"
                    "  [yellow]Ctrl+E[/]   에디터 포커스\n"
                    "  [yellow]Ctrl+L[/]   에디터 비우기\n"
                    "  [yellow]F1[/]       이 도움말\n"
                    "  [yellow]Ctrl+Q[/]   종료\n\n"
                    "[b]인라인 추천 리스트[/] (에디터 바로 아래)\n\n"
                    "  • 에디터 입력에 따라 [b]자동 갱신[/] (popup 아님 — 항상 보임)\n"
                    "  • [yellow]Tab[/]   에디터에서 추천 리스트로 포커스 이동\n"
                    "  • [yellow]↑↓[/]   추천 후보 사이 이동\n"
                    "  • [yellow]Enter[/] 선택 → 에디터 커서 위치에 인서트 + 에디터 복귀\n"
                    "  • [yellow]Esc / Tab[/] 에디터로 복귀 (선택 없이)\n\n"
                    "[b]트리 사용법[/]\n\n"
                    "  ↑↓ 이동, Enter 선택 → 에디터 커서 위치에 인서트.\n"
                    "  테이블 노드 = 테이블명 인서트, 컬럼 노드 = 컬럼명 인서트.\n\n"
                    "[b]자동완성 정책[/] (005 / 006 과 동일)\n\n"
                    "  • FROM / JOIN 다음 → 테이블\n"
                    "  • SELECT 다음 → 컬럼 + * + 함수\n"
                    "  • WHERE / AND / GROUP BY / ORDER BY 다음 → 컬럼\n"
                    "  • table_name. 입력 시 → 해당 테이블 컬럼만 한정\n"
                    "  • 어느 위치든 부분입력 (WHE, GR, JOI 등) → 키워드 매치\n\n"
                    "[dim]Esc 또는 Q 로 닫기[/]"
                ))

    # ── 메인 App ──
    class SQLRunnerApp(App):
        CSS = """
        Screen { layers: base popup; background: $background; }
        #entities { width: 36; border-right: solid $accent; }
        #editor   { height: 14; border: round $accent; }
        #ctx-label {
            padding: 0 1; height: 3; color: $text-muted;
        }
        #results-label { padding: 0 1; color: $text-muted; }
        #results  { height: 1fr; border: round $accent; }
        """

        BINDINGS = [
            # Ctrl+Enter 는 터미널이 보통 Ctrl+J 로 전송 (Unix 관습) — 둘 다 받음
            Binding("ctrl+r,f5,ctrl+enter,ctrl+j",  "run",
                    "▶ 실행", priority=True),
            # Tab 은 에디터 indent 그대로 (override 안 함).
            # 자동완성 popup 은 입력 시 자동, 또는 Ctrl+Space 로 수동 트리거.
            Binding("ctrl+space", "show_popup",   "자동완성", priority=True),
            Binding("ctrl+t",     "focus_tree",   "트리"),
            Binding("ctrl+e",     "focus_editor", "에디터"),
            Binding("ctrl+l",     "clear",        "비우기"),
            Binding("ctrl+s",     "save_csv",     "⬇ CSV"),
            Binding("ctrl+x",     "save_xlsx",    "⬇ Excel",  priority=True),
            Binding("f1",         "help",         "도움말"),
            Binding("ctrl+q",     "quit",         "종료",     priority=True),
        ]

        def __init__(self, *, on_execute, tables, notes, initial_query,
                     app_state):
            super().__init__()
            self.on_execute = on_execute
            self._tables = tables
            self._notes = notes
            self._initial_query = initial_query
            self._current_sugs: list = []   # 인라인 OptionList ↔ 인덱스 매핑
            # SQLRunnerTUI 가 소유한 dict — last_query/last_result/last_error/
            # history 가 들어있다. App 종료 후에도 사용자가 runner.last_result
            # 로 접근 가능하도록 외부 dict 를 그대로 mutate.
            self.app_state = app_state

        def compose(self) -> ComposeResult:
            yield Header(show_clock=False)
            with Horizontal():
                yield Tree("📚 Entities", id="entities")
                with Vertical():
                    # 인라인 SQL syntax highlight 는 tree-sitter + tree_sitter_sql
                    # 패키지가 모두 있어야 동작. 없으면 LanguageDoesNotExist 가
                    # 발생하므로 plain text 로 fallback.
                    try:
                        editor = _SqlTextArea.code_editor(
                            self._initial_query,
                            language="sql",
                            id="editor",
                            soft_wrap=True,
                        )
                    except Exception:
                        editor = _SqlTextArea.code_editor(
                            self._initial_query,
                            id="editor",
                            soft_wrap=True,
                        )
                    yield editor
                    yield Static("", id="ctx-label")
                    yield Static("📤 결과 (Ctrl+R 또는 F5 로 실행)",
                                 id="results-label")
                    yield DataTable(id="results", zebra_stripes=True)
            # 커서 근처에 뜨는 floating 자동완성 popup. 항상 mount 되어 있고
            # display:none 으로 숨겨짐. _show_popup() 에서 위치+visible 토글.
            yield _CursorPopup(id="popup")
            yield Footer()

        def on_mount(self) -> None:
            # entity 트리 채우기
            tree = self.query_one("#entities", Tree)
            tree.show_root = False
            tree.root.expand()
            if not self._tables:
                tree.root.add_leaf("(테이블이 없습니다)")
            else:
                for tname, cols in self._tables.items():
                    label = f"📋 {tname}  ({len(cols)})"
                    if self._notes.get(tname):
                        label += f"  [dim]{self._notes[tname]}[/]"
                    node = tree.root.add(Text.from_markup(label),
                                         data={"kind": "table",
                                               "name": tname},
                                         expand=True)
                    for c in cols:
                        meta = c.get("type", "")
                        doc = c.get("doc", "")
                        leaf_label = f"{c['name']}  [dim]{meta}"
                        if doc:
                            leaf_label += f" · {doc}"
                        leaf_label += "[/]"
                        node.add_leaf(
                            Text.from_markup(leaf_label),
                            data={"kind": "column",
                                  "name": c["name"], "table": tname,
                                  "type": meta, "doc": doc},
                        )

            # 결과 테이블 초기 컬럼
            table = self.query_one("#results", DataTable)
            table.cursor_type = "row"

            # 초기 추천 라인
            self._update_suggest(self._initial_query)

            # 에디터에 초기 포커스
            self.query_one("#editor", TextArea).focus()

        # ── 에디터 텍스트 변경 또는 커서 이동 → 인라인 추천 갱신 ──
        # 005 처럼 커서 위치를 반영하려고 cursor_location 까지의 텍스트를
        # 컨텍스트 감지에 사용. 전체 SQL 은 alias 추출에 사용.
        def on_text_area_changed(self, event: TextArea.Changed) -> None:
            self._refresh_suggest()

        # 마우스/키로 커서만 이동해도 추천이 갱신되도록
        def on_text_area_selection_changed(self, event) -> None:
            self._refresh_suggest()
            # 커서가 움직이면 popup 도 따라가게. 단, popup 이 보이는 동안 Up/
            # Down 등으로 popup 내부를 조작 중일 수 있어 popup 이 visible
            # 이면 위치만 갱신 (focus 빼앗지 않음)
            popup = self.query_one("#popup", _CursorPopup)
            if "visible" in popup.classes:
                self._reposition_popup()

        def _refresh_suggest(self) -> None:
            try:
                ed = self.query_one("#editor", TextArea)
            except Exception:
                return
            full = ed.text
            try:
                line_idx, col = ed.cursor_location
                lines = full.split("\n")
                if line_idx == 0:
                    before_cursor = lines[0][:col] if lines else ""
                else:
                    before_cursor = ("\n".join(lines[:line_idx]) + "\n"
                                     + lines[line_idx][:col]
                                     if line_idx < len(lines) else full)
            except Exception:
                before_cursor = full
            self._update_suggest(before_cursor, full_text=full)

            # popup 자동 트리거 안 함 (Ctrl+Space 만으로 호출).
            # 단, 이미 popup 이 떠 있는 동안엔 글자/커서 변경에 따라
            # 콘텐츠와 위치를 즉시 갱신해 IDE 같은 filter-as-you-type 체감.
            popup = self.query_one("#popup", _CursorPopup)
            if "visible" in popup.classes:
                if self._current_sugs:
                    self._show_popup()
                else:
                    self._hide_popup()

        def _update_suggest(self, text: str,
                              full_text: Optional[str] = None) -> None:
            ctx = detect_context(text)
            ctx_label = {
                "start": "시작", "tables": "테이블", "columns": "컬럼",
                "columns_or_star": "컬럼 / *", "join_continue": "JOIN 계속",
                "from_keyword": "FROM", "number": "숫자", "any": "임의",
                "general": "범용",
            }.get(ctx, ctx)
            self._current_sugs = get_suggestions(
                text, self._tables, full_text=full_text)[:30]

            # 005 처럼 컨텍스트 라벨 아래에 컬러 칩으로 가능한 항목 노출
            # (정보용 — 클릭/포커스 X, Ctrl+Space 누르면 진짜 popup 뜸)
            kind_color = {
                "table": "green", "column": "yellow",
                "keyword": "cyan", "function": "magenta", "star": "white",
            }
            chips: list[str] = []
            for s in self._current_sugs[:14]:
                color = kind_color.get(s["kind"], "white")
                chips.append(f"[{color}]{s['label']}[/]")
            chips_str = "  ".join(chips) if chips else "[dim](추천 없음)[/]"

            self.query_one("#ctx-label", Static).update(Text.from_markup(
                f"💡 [bold cyan]{ctx_label}[/]  "
                f"[dim]· Ctrl+Space 자동완성 popup · "
                f"Tab(에디터) 들여쓰기[/]\n   {chips_str}"
            ))

        # ── floating popup 표시 / 숨기기 / 재위치 ──
        def _show_popup(self) -> None:
            popup = self.query_one("#popup", _CursorPopup)
            if not self._current_sugs:
                self._hide_popup()
                return
            popup.clear_options()
            kind_color = {
                "table": "green", "column": "yellow",
                "keyword": "cyan", "function": "magenta", "star": "white",
            }
            options = []
            for s in self._current_sugs:
                color = kind_color.get(s["kind"], "white")
                meta = s.get("meta", "") or ""
                row = (f"[{color}]{s['label']:<20}[/] "
                       f"[dim]{s['kind'][:6]} {meta}[/]")
                options.append(Option(Text.from_markup(row)))
            popup.add_options(options)
            try:
                popup.highlighted = 0
            except Exception:
                pass
            self._reposition_popup()
            popup.add_class("visible")
            popup.focus()

        def _reposition_popup(self) -> None:
            try:
                ed = self.query_one("#editor", TextArea)
                popup = self.query_one("#popup", _CursorPopup)
                off = ed.cursor_screen_offset
                # 커서 한 줄 아래에 표시 (글자 너비 기준)
                popup.styles.offset = (off.x, off.y + 1)
            except Exception:
                pass

        def _hide_popup(self) -> None:
            popup = self.query_one("#popup", _CursorPopup)
            popup.remove_class("visible")
            try:
                self.query_one("#editor", _SqlTextArea).focus()
            except Exception:
                pass

        def action_show_popup(self) -> None:
            """수동 트리거 (Ctrl+Space)."""
            self._refresh_suggest()
            if self._current_sugs:
                self._show_popup()

        # ── popup OptionList 선택 → 에디터 커서 위치에 인서트 ──
        def on_option_list_option_selected(
            self, event: OptionList.OptionSelected
        ) -> None:
            if event.option_list.id != "popup":
                return
            idx = event.option_index
            if 0 <= idx < len(self._current_sugs):
                snippet = self._current_sugs[idx]["value"]
                self._insert_at_cursor(snippet)
            self._hide_popup()

        # ── 트리 노드 선택 → 에디터 커서 위치에 이름 인서트 ──
        # 테이블 / 컬럼 구분 없이 이름만 단순 인서트.
        def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
            data = event.node.data
            if not data:
                return
            self._insert_at_cursor(data["name"])

        def _insert_at_cursor(self, snippet: str) -> None:
            editor = self.query_one("#editor", TextArea)
            # 마지막 부분 단어가 prefix 면 치환, 아니면 적절한 구분자와 인서트
            text_before = editor.get_text_range(
                start=(0, 0), end=editor.cursor_location
            )
            m = re.search(r"([\w_.]+)$", text_before)
            if m and snippet.lower().startswith(m.group(1).lower()):
                # cursor 직전 단어를 선택 후 교체
                line, col = editor.cursor_location
                start_col = col - len(m.group(1))
                editor.replace(
                    snippet,
                    start=(line, start_col),
                    end=(line, col),
                )
            else:
                # 적절한 구분자 처리 (직전이 식별자 글자면 공백 한 칸)
                sep = ""
                if text_before and not text_before[-1] in (" ", "\n", "\t",
                                                            "(", ",", "."):
                    sep = " "
                editor.insert(sep + snippet)
            editor.focus()

        # ── ▶ 실행 ──
        def action_run(self) -> None:
            editor = self.query_one("#editor", TextArea)
            sql = editor.text
            table = self.query_one("#results", DataTable)
            label = self.query_one("#results-label", Static)
            table.clear(columns=True)

            # runner 객체에 마지막 실행 SQL 기록 (실패해도 query 는 남김)
            self.app_state["last_query"] = sql

            if not sql.strip():
                label.update("[red]⚠ SQL 이 비어있습니다[/]")
                return
            if self.on_execute is None:
                label.update("[yellow]on_execute 콜백이 등록되지 않았습니다[/]")
                table.add_columns("SQL")
                table.add_row(sql)
                return

            label.update("[dim]실행 중...[/]")
            try:
                result = self.on_execute(sql)
            except Exception as e:
                label.update(
                    f"[red]❌ {type(e).__name__}: {e}[/]"
                )
                self.app_state["last_error"] = e
                self.app_state["last_result"] = None
                self.app_state["history"].append(
                    {"query": sql, "result": None, "error": e})
                return
            self.app_state["last_error"] = None
            self.app_state["last_result"] = result
            self.app_state["history"].append(
                {"query": sql, "result": result, "error": None})
            self._render_result(result, label, table)

        # ── ⬇ CSV / Excel 저장 (마지막 실행 결과를 파일로) ──
        def action_save_csv(self) -> None:
            self._save_result("csv")

        def action_save_xlsx(self) -> None:
            self._save_result("xlsx")

        def _save_result(self, fmt: str) -> None:
            import datetime, os
            res = self.app_state.get("last_result")
            if res is None:
                self.notify("⚠ 다운로드할 결과가 없습니다. ▶ 실행 후 시도하세요.",
                            severity="warning")
                return
            try:
                import pandas as pd
            except ImportError:
                self.notify("⚠ pandas 미설치 — CSV/Excel 저장은 pandas 필요.",
                            severity="error")
                return
            df = res
            if isinstance(df, list) and df and isinstance(df[0], dict):
                df = pd.DataFrame(df)
            if not isinstance(df, pd.DataFrame):
                self.notify(f"⚠ 결과가 DataFrame/list[dict] 형식이 아니라 저장 불가 "
                            f"(type={type(res).__name__}).", severity="warning")
                return
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            fname = f"sql_result_{ts}.{fmt}"
            path = os.path.abspath(fname)
            try:
                if fmt == "csv":
                    df.to_csv(path, index=False, encoding="utf-8-sig")
                else:
                    try:
                        df.to_excel(path, index=False, engine="openpyxl")
                    except (ImportError, ValueError):
                        self.notify("⚠ Excel 엔진 (openpyxl) 미설치. CSV 사용 권장.",
                                    severity="error")
                        return
            except Exception as e:
                self.notify(f"❌ {type(e).__name__}: {e}", severity="error")
                return
            self.notify(
                f"✓ {fname} 저장 ({len(df)} rows × {len(df.columns)} cols)\n"
                f"  → {path}",
                severity="information",
                timeout=5,
            )

        def _render_result(self, result, label, table) -> None:
            from rich.markup import escape as rich_escape
            if result is None:
                label.update("[green]✓ 실행 완료 (반환값 없음)[/]")
                return
            try:
                import pandas as pd
                if isinstance(result, pd.DataFrame):
                    self._fill_from_dataframe(result, label, table)
                    return
                if isinstance(result, list) and result \
                        and isinstance(result[0], dict):
                    df = pd.DataFrame(result)
                    self._fill_from_dataframe(df, label, table,
                                              note="list[dict] → DataFrame")
                    return
            except ImportError:
                pass
            if isinstance(result, list):
                if not result:
                    label.update("[green]✓ 실행 완료 (행 없음)[/]")
                    return
                if isinstance(result[0], (tuple, list)):
                    ncols = len(result[0])
                    table.add_columns(*[f"col{i}" for i in range(ncols)])
                    for row in result:
                        table.add_row(*[self._fmt_cell(v) for v in row])
                    label.update(
                        f"[green]✓ {len(result)} rows × {ncols} cols[/]"
                    )
                    return
            # 그 외 — repr 한 줄로
            table.add_columns("Result")
            table.add_row(rich_escape(repr(result)))
            label.update("[green]✓ 실행 완료[/]")

        def _fill_from_dataframe(self, df, label, table, note: str = "") -> None:
            cols = [str(c) for c in df.columns]
            if not cols:
                label.update("[green]✓ 실행 완료 (컬럼 없음)[/]")
                return
            table.add_columns(*cols)
            for _, row in df.iterrows():
                table.add_row(*[self._fmt_cell(v) for v in row])
            extra = f" · {note}" if note else ""
            label.update(
                f"[green]✓ {len(df)} rows × {len(cols)} cols[/]{extra}"
            )

        def _fmt_cell(self, v: Any) -> str:
            if v is None:
                return "—"
            try:
                import pandas as pd
                if pd.isna(v):
                    return "—"
            except Exception:
                pass
            s = str(v)
            if len(s) > 80:
                s = s[:77] + "..."
            return s

        # ── 기타 액션 ──
        def action_clear(self) -> None:
            editor = self.query_one("#editor", TextArea)
            editor.text = ""
            editor.focus()

        def action_focus_tree(self) -> None:
            self.query_one("#entities", Tree).focus()

        def action_focus_editor(self) -> None:
            self.query_one("#editor", TextArea).focus()

        def action_help(self) -> None:
            self.push_screen(_HelpScreen())

    return SQLRunnerApp(
        on_execute=on_execute,
        tables=tables,
        notes=notes,
        initial_query=initial_query,
        app_state=(app_state if app_state is not None
                   else {"last_query": None, "last_result": None,
                         "last_error": None, "history": []}),
    )


# ===== SQLRunnerTUI builder (005 / 006 와 동일 API) =====

class SQLRunnerTUI:
    """터미널용 풀스크린 SQL 편집기 + 실행자.

    Args:
        on_execute: ``f(sql: str) -> Any`` 콜백. ▶ 실행 시 호출되고,
            반환값이 None 이 아니면 DataTable 에 표시.
            DataFrame / list[dict] 는 자동 표 변환.
    """

    def __init__(self,
                 on_execute: Optional[Callable[[str], Any]] = None) -> None:
        self.tables: dict[str, list[dict]] = {}
        self.notes: dict[str, str] = {}
        self.initial_query: str = ""
        self.on_execute = on_execute

        # 후속 분석을 위한 실행 상태 (App 종료 후에도 runner.last_result 등으로
        # 접근 가능). App 이 같은 dict 를 mutate.
        self._state: dict = {
            "last_query": None,
            "last_result": None,
            "last_error": None,
            "history": [],
        }

    # ----- runner 객체에 노출되는 후속 분석 attribute -----

    @property
    def last_query(self) -> Optional[str]:
        return self._state["last_query"]

    @property
    def last_result(self) -> Any:
        return self._state["last_result"]

    @property
    def last_error(self) -> Optional[BaseException]:
        return self._state["last_error"]

    @property
    def history(self) -> list:
        return self._state["history"]

    @property
    def result(self) -> Any:
        """last_result alias."""
        return self._state["last_result"]

    @classmethod
    def with_sqlite(cls, db_path: str) -> "SQLRunnerTUI":
        """SQLite + pandas 자동 wrap 편의 메서드 (TUI 는 단일 스레드라 thread
        문제 없음 — 06/07 과 달리 connect 매번 새로 안 열어도 되지만
        패턴 일관성을 위해 with-block 사용)."""
        def _run(sql: str) -> Any:
            try:
                import pandas as pd
            except ImportError as e:
                raise RuntimeError(
                    "with_sqlite 는 pandas 가 필요합니다."
                ) from e
            with sqlite3.connect(db_path) as conn:
                return pd.read_sql(sql, conn)
        runner = cls(on_execute=_run)
        runner.from_sqlite(db_path)
        return runner

    def add_table(self, name: str,
                  columns: Iterable[ColumnSpec],
                  description: str = "") -> "SQLRunnerTUI":
        self.tables[name] = [_normalize_column(c) for c in columns]
        if description:
            self.notes[name] = description
        return self

    def from_dict(self,
                  schema: Mapping[str, Iterable[ColumnSpec]]) -> "SQLRunnerTUI":
        for tname, cols in schema.items():
            self.add_table(tname, cols)
        return self

    def from_sqlite(self, path: str) -> "SQLRunnerTUI":
        conn = sqlite3.connect(path)
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            )
            tnames = [row[0] for row in cur.fetchall()]
            for t in tnames:
                cur.execute(f"PRAGMA table_info({t})")
                cols: list[ColumnSpec] = []
                for _cid, cname, ctype, _nn, _dflt, pk in cur.fetchall():
                    cols.append({"name": cname,
                                 "type": ctype or "",
                                 "doc": "PK" if pk else ""})
                self.tables[t] = [_normalize_column(c) for c in cols]
        finally:
            conn.close()
        return self

    def from_dataframes(self,
                        dataframes: Mapping[str, Any]) -> "SQLRunnerTUI":
        for name, df in dataframes.items():
            cols: list[ColumnSpec] = []
            try:
                for col, dtype in zip(df.columns, df.dtypes):
                    cols.append({"name": str(col),
                                 "type": str(dtype), "doc": ""})
            except AttributeError as e:
                raise TypeError(
                    f"from_dataframes 의 값은 pandas.DataFrame 이어야 합니다 ({name})"
                ) from e
            self.tables[name] = [_normalize_column(c) for c in cols]
        return self

    def set_query(self, query: str) -> "SQLRunnerTUI":
        self.initial_query = query
        return self

    def run(self) -> None:
        """풀스크린 TUI 진입. 종료 시 정상 반환.

        TUI 종료 후에도 ``runner.last_query`` / ``runner.last_result``
        / ``runner.history`` 로 마지막 실행 상태에 접근 가능.
        """
        app = _build_app(
            on_execute=self.on_execute,
            tables=self.tables,
            notes=self.notes,
            initial_query=self.initial_query,
            app_state=self._state,   # App 이 같은 dict 를 mutate
        )
        app.run()


# ===== __main__ — 자체 데모 =====

if __name__ == "__main__":
    import sys, os, tempfile

    if "--check" in sys.argv:
        # CLI 단위 검증 (textual 앱 띄우지 않음)
        runner = SQLRunnerTUI()
        runner.add_table("users", ["id", "name", "email"], "사용자")
        runner.add_table("orders", [("id","INT"),("user_id","INT")])
        print(f"등록된 테이블: {list(runner.tables.keys())}")
        ctx = detect_context("SELECT name FROM users WHERE ")
        sugs = get_suggestions("SELECT name FROM users WHERE ", runner.tables)
        print(f"detect_context: {ctx}")
        print(f"top suggestions: {[s['label'] for s in sugs[:8]]}")
        sys.exit(0)

    # 데모 DB 생성
    db_path = os.path.join(tempfile.gettempdir(), "sql_tui_demo.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT,
                            region TEXT, plan_type TEXT);
        CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id INTEGER,
                             amount REAL, status TEXT);
        INSERT INTO users VALUES
            (1,'김알리스','서울','pro'),
            (2,'이밥',  '부산','free'),
            (3,'박찰리','대구','pro');
        INSERT INTO orders VALUES
            (1,1,39000,'paid'), (2,1,12500,'paid'),
            (3,2,29000,'paid'), (4,3,15000,'cancelled');
        """)

    runner = SQLRunnerTUI.with_sqlite(db_path)
    runner.set_query(
        "-- Ctrl+R 또는 F5 로 실행 · F1 도움말\n"
        "SELECT u.name, u.region, SUM(o.amount) AS total\n"
        "FROM users u JOIN orders o ON o.user_id = u.id\n"
        "WHERE o.status = 'paid'\n"
        "GROUP BY u.id ORDER BY total DESC;"
    )
    runner.run()
