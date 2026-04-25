"""
SQL Runner for Jupyter Notebook (ipywidgets 기반, 실행 가능).

005-sql-editor-notebook 과 같은 컨셉이지만 다음이 다릅니다:
  · 005 = HTML/JS 단독 → 자동완성 popup, 커서 위치 인서트, 인라인 자유도가 높음
          (Python 콜백 호출 불가)
  · 006 = ipywidgets 기반 → ▶ 실행 버튼이 Python 콜백을 호출, Output 위젯에
          결과 표시. 라이브 SQL 구문 강조 프리뷰 추가. 단 inline popup
          autocomplete 는 없고 추천이 클릭 가능한 Button 으로 노출.

라이선스: MIT (오리지널 구현)
생성: Code Conversion Agent

핵심 기능
--------
  1) 좌측 entity 트리 (테이블/컬럼이 ipywidgets.Button) — 클릭 → 쿼리에 삽입
     (마지막 부분 단어가 매치되면 자동 치환, 아니면 끝에 append)
  2) 우측 Textarea 에디터 — Enter 가 자연스럽게 newline 으로 동작 (textarea 기본)
  3) 라이브 SQL 구문 강조 프리뷰 — 키워드/함수/문자열/숫자/주석을 색상 분리해
     `<pre>` 로 렌더 (Python 사이드 미니 lexer, 외부 의존 없음)
  4) 컨텍스트 인식 추천 패널 — 현재 직전 anchor 키워드(FROM/SELECT/WHERE/...)
     에 따라 추천 종류 분기 → ipywidgets.Button 칩으로 노출, 클릭하면 삽입
  5) ▶ 실행 버튼 → 사용자가 주입한 `on_execute(sql)` 콜백 호출, 반환값을
     Output 위젯에 display (DataFrame 도 그대로 표 렌더)
  6) 외부 네트워크 / CDN / 바이너리 영속화 일절 없음 — single-file 반입 가능

사용 예시
--------
    import sqlite3, pandas as pd
    from sql_runner import SQLRunner

    conn = sqlite3.connect("./local.db")
    runner = SQLRunner(on_execute=lambda sql: pd.read_sql(sql, conn))
    runner.from_sqlite("./local.db")
    runner.set_query("SELECT * FROM users LIMIT 10;")
    runner.show()
"""
from __future__ import annotations

import re
import sqlite3
from html import escape
from typing import Any, Callable, Iterable, Mapping, Optional, Union

# ===== 타입 alias =====

ColumnSpec = Union[
    str,
    tuple,
    Mapping[str, Any],
]


# ===== SQL 키워드 / 함수 (highlight + 추천에 공용) =====

_KEYWORDS = {
    "SELECT", "FROM", "WHERE", "AND", "OR", "NOT", "IN", "LIKE", "IS", "NULL",
    "JOIN", "INNER", "LEFT", "RIGHT", "FULL", "OUTER", "ON", "USING", "AS",
    "GROUP", "ORDER", "BY", "HAVING", "LIMIT", "OFFSET",
    "DISTINCT", "ALL", "UNION", "EXCEPT", "INTERSECT",
    "INSERT", "UPDATE", "DELETE", "INTO", "VALUES", "SET",
    "CREATE", "ALTER", "DROP", "TABLE", "INDEX", "VIEW", "WITH", "RECURSIVE",
    "CASE", "WHEN", "THEN", "ELSE", "END",
    "ASC", "DESC", "BETWEEN", "EXISTS",
    "TRUE", "FALSE",
}
_FUNCTIONS = {
    "COUNT", "SUM", "AVG", "MIN", "MAX",
    "COALESCE", "NULLIF", "IFNULL",
    "UPPER", "LOWER", "LENGTH", "SUBSTR", "TRIM", "REPLACE",
    "ROUND", "FLOOR", "CEIL", "ABS",
    "DATE", "DATETIME", "STRFTIME", "JULIANDAY",
    "CAST",
}

# 컨텍스트 anchor — 직전에 등장하면 다음 토큰의 종류를 강하게 결정함
_ANCHORS = {
    "SELECT", "FROM", "WHERE", "JOIN", "ON", "AND", "OR",
    "GROUP", "ORDER", "HAVING", "LIMIT", "BY",
    "INSERT", "UPDATE", "DELETE", "SET", "INTO", "VALUES",
    "INNER", "LEFT", "RIGHT", "FULL",
    "UNION", "EXCEPT", "INTERSECT",
    "AS", "WITH",
}


# ===== 1. 라이브 SQL 구문 강조 (Python 미니 lexer → 인라인 스타일 HTML) =====

def highlight_sql_html(text: str) -> str:
    """SQL 텍스트를 색상 강조된 self-contained HTML 로 변환.

    외부 라이브러리(pygments 등) 없이 미니 lexer 로 키워드/함수/문자열/숫자/
    주석을 분리. 모든 색상은 인라인 스타일 → ipywidgets.HTML 위젯에서 그대로
    렌더 가능.
    """
    if not text:
        return (
            '<pre style="background:#1e1e1e;color:#888;'
            'font-family:\'SF Mono\',Menlo,Consolas,monospace;'
            'font-size:13px;padding:10px 12px;margin:0;border-radius:4px;'
            'font-style:italic">(쿼리 없음)</pre>'
        )

    parts: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]

        # 라인 코멘트 -- ...
        if ch == "-" and i + 1 < n and text[i + 1] == "-":
            j = text.find("\n", i)
            if j == -1:
                j = n
            parts.append(
                f'<span style="color:#6a9955;font-style:italic">'
                f'{escape(text[i:j])}</span>'
            )
            i = j
            continue

        # 블록 코멘트 /* ... */
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            if j == -1:
                j = n
            else:
                j += 2
            parts.append(
                f'<span style="color:#6a9955;font-style:italic">'
                f'{escape(text[i:j])}</span>'
            )
            i = j
            continue

        # 문자열 리터럴 '...'
        if ch == "'":
            j = i + 1
            while j < n:
                if text[j] == "'" and (j + 1 >= n or text[j + 1] != "'"):
                    j += 1
                    break
                j += 1
            parts.append(
                f'<span style="color:#ce9178">{escape(text[i:j])}</span>'
            )
            i = j
            continue

        # 따옴표로 감싼 식별자 "..."
        if ch == '"':
            j = text.find('"', i + 1)
            if j == -1:
                j = n
            else:
                j += 1
            parts.append(
                f'<span style="color:#9cdcfe">{escape(text[i:j])}</span>'
            )
            i = j
            continue

        # 숫자
        m = re.match(r"\d+(\.\d+)?", text[i:])
        if m:
            parts.append(
                f'<span style="color:#b5cea8">{escape(m.group(0))}</span>'
            )
            i += len(m.group(0))
            continue

        # 식별자 / 키워드 / 함수
        m = re.match(r"[A-Za-z_][A-Za-z0-9_]*", text[i:])
        if m:
            tok = m.group(0)
            up = tok.upper()
            if up in _KEYWORDS:
                parts.append(
                    f'<span style="color:#569cd6;font-weight:600">'
                    f'{escape(tok)}</span>'
                )
            elif up in _FUNCTIONS:
                parts.append(
                    f'<span style="color:#dcdcaa">{escape(tok)}</span>'
                )
            else:
                parts.append(
                    f'<span style="color:#d4d4d4">{escape(tok)}</span>'
                )
            i += len(tok)
            continue

        # 그 외 (공백, 구두점)
        parts.append(escape(ch))
        i += 1

    body = "".join(parts)
    return (
        '<pre style="background:#1e1e1e;color:#d4d4d4;'
        'font-family:\'SF Mono\',Menlo,Consolas,monospace;'
        'font-size:13px;line-height:1.5;padding:10px 12px;margin:0;'
        'border-radius:4px;overflow-x:auto;white-space:pre-wrap;'
        f'word-break:break-word">{body}</pre>'
    )


# ===== 2. 컨텍스트 감지 + 추천 (Python 사이드, 005 의 JS 로직과 동일 골격) =====

def detect_context(text: str) -> str:
    """직전 anchor 키워드로 추천 종류를 결정."""
    s = re.sub(r"--[^\n]*", " ", text)
    s = re.sub(r"/\*.*?\*/", " ", s, flags=re.DOTALL)
    s = re.sub(r"'[^']*'", " ", s)
    s = re.sub(r'"[^"]*"', " ", s)
    tokens = s.split()
    if not tokens:
        return "start"

    last = None
    last_idx = -1
    for i in range(len(tokens) - 1, -1, -1):
        tu = tokens[i].upper()
        if tu in _ANCHORS:
            last = tu
            last_idx = i
            break
    if last is None:
        return "start"

    # GROUP BY / ORDER BY 합성
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
        "LIMIT": "number",
        "DELETE": "from_keyword",
        "VALUES": "any", "AS": "any", "WITH": "any",
    }
    return MAP.get(last, "general")


def get_suggestions(text: str, tables: Mapping[str, list[dict]]) -> list[dict]:
    """현재 컨텍스트에 맞는 추천 후보 리스트.

    005 의 JS getSuggestions() 와 동일한 정책. 단 cursor 위치를 알 수 없어
    'table.' qualifier 검사는 마지막 부분 단어 기준으로만 수행.
    """
    ctx = detect_context(text)

    # 마지막 부분 단어 (cursor 가 끝이라고 가정)
    m = re.search(r"([\w_.]+)$", text)
    last_word = m.group(1) if m else ""
    last_lower = last_word.lower()

    # table. qualifier 우선 처리
    if "." in last_word:
        dot_idx = last_word.index(".")
        tname = last_word[:dot_idx]
        col_prefix = last_word[dot_idx + 1:].lower()
        if tname in tables:
            cols = tables[tname]
            return [
                {
                    "value": f"{tname}.{c['name']}",
                    "label": c["name"],
                    "kind": "column",
                    "meta": c.get("type", "") or tname,
                }
                for c in cols
                if c["name"].lower().startswith(col_prefix)
            ][:30]

    cands: list[dict] = []

    if ctx in ("tables", "general", "start"):
        for tname in tables.keys():
            cands.append({"value": tname, "label": tname,
                          "kind": "table", "meta": "table"})

    if ctx in ("columns", "columns_or_star", "general", "start"):
        seen: set[str] = set()
        for tname, cols in tables.items():
            for c in cols:
                if c["name"] in seen:
                    continue
                seen.add(c["name"])
                meta = (c.get("type", "") + " · " if c.get("type") else "") + tname
                cands.append({"value": c["name"], "label": c["name"],
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

    if ctx in ("general", "start"):
        for kw in sorted(_KEYWORDS):
            cands.append({"value": kw, "label": kw,
                          "kind": "keyword", "meta": "kw"})
        for fn in sorted(_FUNCTIONS):
            cands.append({"value": fn + "(", "label": fn + "(",
                          "kind": "function", "meta": "fn"})

    if ctx in ("columns", "columns_or_star"):
        for fn in sorted(_FUNCTIONS):
            cands.append({"value": fn + "(", "label": fn + "(",
                          "kind": "function", "meta": "fn"})

    # 마지막 부분 단어로 substring 매칭 필터
    if last_lower:
        cands = [c for c in cands if last_lower in c["label"].lower()]

    return cands[:30]


# ===== 3. 컬럼 스펙 정규화 =====

def _normalize_column(c: ColumnSpec) -> dict:
    if isinstance(c, str):
        return {"name": c, "type": "", "doc": ""}
    if isinstance(c, tuple):
        return {
            "name": c[0],
            "type": c[1] if len(c) > 1 else "",
            "doc": c[2] if len(c) > 2 else "",
        }
    if isinstance(c, Mapping):
        return {
            "name": str(c["name"]),
            "type": str(c.get("type", "")),
            "doc": str(c.get("doc", "")),
        }
    raise TypeError(f"알 수 없는 컬럼 스펙 형식: {type(c).__name__}")


# ===== 4. SQLRunner 클래스 =====

class SQLRunner:
    """ipywidgets 기반 SQL 편집기 + 실행 위젯.

    Args:
        on_execute: ``f(sql: str) -> Any`` 형태의 콜백. ▶ 실행 버튼 클릭 시
            현재 쿼리 텍스트와 함께 호출된다. 반환값이 None 이 아니면
            Output 위젯에 ``display(...)`` 로 표시 (DataFrame 등은 표 렌더).
            None 이면 콜백 미등록 안내 메시지만 표시.
    """

    def __init__(self,
                 on_execute: Optional[Callable[[str], Any]] = None) -> None:
        self.tables: dict[str, list[dict]] = {}
        self.notes: dict[str, str] = {}
        self.initial_query: str = ""
        self.on_execute = on_execute

        # 위젯 ref (show 에서 채움)
        self._textarea = None
        self._highlight = None
        self._suggest_box = None
        self._output = None

    # ----- 편의 생성자 (thread-safe sqlite 패턴 자동 적용) -----

    @classmethod
    def with_sqlite(cls, db_path: str) -> "SQLRunner":
        """SQLite DB 경로 하나로 thread-safe 한 SQLRunner 를 즉시 구성.

        ipywidgets 버튼 콜백은 Jupyter 커널의 comm/IO 스레드에서 실행되어
        외부 셀에서 만든 sqlite3.Connection 과 thread 가 다를 수 있다 (그
        경우 ProgrammingError: SQLite objects created in a thread can only
        be used in that same thread). 이 헬퍼는 매 호출마다 새 connect 를
        열고 닫아 thread 문제를 회피한다.

        사용:
            runner = SQLRunner.with_sqlite("./demo.db")
            runner.show()

        pandas 는 lazy import. 미설치면 ImportError 만 미루고 호출 시점에
        명확히 안내.
        """
        def _run(sql: str) -> Any:
            try:
                import pandas as pd
            except ImportError as e:  # pragma: no cover
                raise RuntimeError(
                    "with_sqlite 는 pandas 가 필요합니다. "
                    "직접 on_execute 콜백을 작성하거나 pandas 설치 후 재시도."
                ) from e
            with sqlite3.connect(db_path) as conn:
                return pd.read_sql(sql, conn)

        runner = cls(on_execute=_run)
        runner.from_sqlite(db_path)
        return runner

    # ----- 스키마 등록 (005 와 동일 API) -----

    def add_table(self, name: str,
                  columns: Iterable[ColumnSpec],
                  description: str = "") -> "SQLRunner":
        self.tables[name] = [_normalize_column(c) for c in columns]
        if description:
            self.notes[name] = description
        return self

    def from_dict(self, schema: Mapping[str, Iterable[ColumnSpec]]) -> "SQLRunner":
        for tname, cols in schema.items():
            self.add_table(tname, cols)
        return self

    def from_sqlite(self, path: str) -> "SQLRunner":
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
                    cols.append({
                        "name": cname,
                        "type": ctype or "",
                        "doc": "PK" if pk else "",
                    })
                self.tables[t] = [_normalize_column(c) for c in cols]
        finally:
            conn.close()
        return self

    def from_dataframes(self, dataframes: Mapping[str, Any]) -> "SQLRunner":
        for name, df in dataframes.items():
            cols: list[ColumnSpec] = []
            try:
                for col, dtype in zip(df.columns, df.dtypes):
                    cols.append({"name": str(col), "type": str(dtype), "doc": ""})
            except AttributeError as e:
                raise TypeError(
                    f"from_dataframes 의 값은 pandas.DataFrame 이어야 합니다 ({name})"
                ) from e
            self.tables[name] = [_normalize_column(c) for c in cols]
        return self

    def set_query(self, query: str) -> "SQLRunner":
        self.initial_query = query
        return self

    # ----- 렌더 -----

    def show(self) -> None:
        """Jupyter 셀에 ipywidgets 합성 위젯을 렌더."""
        try:
            import ipywidgets as W
            from IPython.display import display, HTML
        except ImportError as e:
            raise RuntimeError(
                "show() 는 Jupyter + ipywidgets 가 필요합니다."
            ) from e

        # ── 좌측 entity 트리 (테이블/컬럼 모두 Button) ──
        tree_children: list = [
            W.HTML(
                '<div style="padding:8px 10px;font-weight:600;font-size:12px;'
                'background:#eef0f3;border-bottom:1px solid #d8dde1">'
                '📚 Entities</div>'
            ),
        ]
        if not self.tables:
            tree_children.append(W.HTML(
                '<div style="padding:12px;color:#888;font-size:11px">'
                '등록된 테이블이 없습니다. <br>'
                '<code>add_table(...)</code> / <code>from_dict(...)</code> 로 추가하세요.'
                '</div>'
            ))
        else:
            for tname, cols in self.tables.items():
                # 테이블 라벨 (클릭 → 테이블명 삽입)
                tbtn = W.Button(
                    description=f"📋 {tname}  ({len(cols)})",
                    layout=W.Layout(width="218px", height="26px",
                                    margin="2px 0 1px 0"),
                )
                tbtn.style.button_color = "#fafbfc"
                tbtn.on_click(self._make_inserter(tname))
                tree_children.append(tbtn)

                if self.notes.get(tname):
                    tree_children.append(W.HTML(
                        f'<div style="padding:0 8px 2px 18px;font-size:10px;'
                        f'color:#6c757d;font-style:italic">'
                        f'{escape(self.notes[tname])}</div>'
                    ))

                col_btns: list = []
                for c in cols:
                    tooltip = c["name"]
                    if c.get("type"):
                        tooltip += f" : {c['type']}"
                    if c.get("doc"):
                        tooltip += f" — {c['doc']}"
                    cbtn = W.Button(
                        description=c["name"],
                        tooltip=tooltip,
                        layout=W.Layout(margin="1px", width="auto", height="22px"),
                    )
                    cbtn.style.button_color = "#ffffff"
                    cbtn.on_click(self._make_inserter(c["name"]))
                    col_btns.append(cbtn)
                tree_children.append(W.HBox(
                    col_btns,
                    layout=W.Layout(flex_flow="row wrap",
                                    padding="0 8px 6px 14px"),
                ))

        tree = W.VBox(
            tree_children,
            layout=W.Layout(width="240px", overflow="auto",
                            max_height="640px",
                            border="1px solid #c8ccd0",
                            border_radius="4px"),
        )

        # ── 에디터 textarea ──
        self._textarea = W.Textarea(
            value=self.initial_query,
            placeholder="SELECT ...",
            layout=W.Layout(width="100%", height="180px"),
        )

        # ── 라이브 syntax 강조 프리뷰 ──
        self._highlight = W.HTML(value=highlight_sql_html(self.initial_query))

        # ── 추천 패널 ──
        self._suggest_box = W.HBox(
            layout=W.Layout(flex_flow="row wrap", padding="2px 0",
                            min_height="32px"),
        )

        # ── 액션 버튼 + Output ──
        run_btn = W.Button(description="▶ 실행 (Run)",
                           button_style="primary",
                           layout=W.Layout(width="auto"))
        copy_btn = W.Button(description="📋 SQL 복사",
                            layout=W.Layout(width="auto"))
        clear_btn = W.Button(description="🗑 지우기",
                             layout=W.Layout(width="auto"))
        run_btn.on_click(self._on_run)
        copy_btn.on_click(self._on_copy)
        clear_btn.on_click(self._on_clear)
        actions = W.HBox(
            [run_btn, copy_btn, clear_btn],
            layout=W.Layout(padding="4px 0"),
        )

        self._output = W.Output(
            layout=W.Layout(border="1px solid #d8dde1",
                            max_height="420px", overflow="auto",
                            padding="4px"),
        )

        # ── 이벤트 hookup ──
        self._textarea.observe(self._on_text_change, names="value")
        # 초기 추천 렌더
        self._update_suggest(self.initial_query)

        # ── CSS 주입 (Textarea 다크 모노스페이스) ──
        css = W.HTML(self._editor_css())

        # ── 우측 패널 조립 ──
        right = W.VBox([
            W.HTML(
                '<div style="padding:5px 10px;background:#eef0f3;'
                'border:1px solid #d8dde1;border-bottom:0;font-size:11px">'
                '<b>SQL Runner</b> · 좌측 클릭 → 쿼리에 삽입 · '
                '에디터에서 Enter = 줄바꿈 · ▶ 실행 으로 콜백 호출'
                '</div>'
            ),
            self._textarea,
            W.HTML(
                '<div style="padding:3px 10px;background:#f7f8fa;'
                'border:1px solid #d8dde1;border-top:0;border-bottom:0;'
                'font-size:11px;color:#6c757d">'
                '🎨 라이브 구문 강조 미리보기'
                '</div>'
            ),
            self._highlight,
            W.HTML(
                '<div style="padding:3px 10px;background:#f7f8fa;'
                'border:1px solid #d8dde1;border-top:0;border-bottom:0;'
                'font-size:11px;color:#6c757d">'
                '💡 추천 (현재 컨텍스트 기반 · 클릭하면 삽입)'
                '</div>'
            ),
            self._suggest_box,
            actions,
            W.HTML(
                '<div style="padding:3px 10px;background:#f7f8fa;'
                'border:1px solid #d8dde1;border-top:0;border-bottom:0;'
                'font-size:11px;color:#6c757d">'
                '📤 실행 결과'
                '</div>'
            ),
            self._output,
        ], layout=W.Layout(flex="1", min_width="0"))

        # ── 최종 레이아웃 ──
        layout = W.HBox([tree, right], layout=W.Layout(width="100%"))

        display(css)
        display(layout)

    # ----- 내부 헬퍼 -----

    def _editor_css(self) -> str:
        """Textarea 다크 monospace 스타일 (셀별 1회 주입)."""
        return (
            '<style>'
            '.widget-textarea textarea {'
            ' font-family: "SF Mono", Menlo, Consolas, monospace !important;'
            ' font-size: 13px !important;'
            ' background: #1e1e1e !important;'
            ' color: #d4d4d4 !important;'
            ' line-height: 1.55 !important;'
            ' padding: 10px 12px !important;'
            ' border: 1px solid #c8ccd0 !important;'
            ' border-radius: 0 !important;'
            ' tab-size: 2 !important;'
            '}'
            '</style>'
        )

    def _make_inserter(self, snippet: str) -> Callable[[Any], None]:
        """버튼 click 핸들러 생성 — 마지막 부분 단어를 치환하거나 끝에 append."""
        def _handler(_btn: Any) -> None:
            if self._textarea is None:
                return
            current = self._textarea.value
            # 마지막 부분 단어 (식별자 + 점) 검사
            m = re.search(r"([\w_.]+)$", current)
            if m:
                last_word = m.group(1)
                # snippet 이 마지막 단어로 시작하면 치환
                if (snippet.lower().startswith(last_word.lower())
                        and len(last_word) > 0):
                    self._textarea.value = current[: m.start()] + snippet
                    return
                # table.col 형태에서 점 뒤만 필터된 경우도 자연스럽게 추가
            # 아니면 적절한 구분자와 함께 끝에 append
            if not current:
                self._textarea.value = snippet
                return
            sep = ""
            if current[-1] not in (" ", "\n", "\t", "(", ",", "."):
                sep = " "
            self._textarea.value = current + sep + snippet
        return _handler

    def _on_text_change(self, change: Mapping[str, Any]) -> None:
        new_text = change.get("new", "")
        self._highlight.value = highlight_sql_html(new_text)
        self._update_suggest(new_text)

    def _update_suggest(self, text: str) -> None:
        import ipywidgets as W
        ctx = detect_context(text)
        ctx_label = {
            "start": "시작",
            "tables": "테이블",
            "columns": "컬럼",
            "columns_or_star": "컬럼 / *",
            "join_continue": "JOIN 계속",
            "from_keyword": "FROM",
            "number": "숫자",
            "any": "임의",
            "general": "범용",
        }.get(ctx, ctx)

        children: list = [
            W.HTML(
                f'<span style="display:inline-block;padding:3px 9px;'
                f'margin:2px 6px 2px 0;background:#fff;'
                f'border:1px solid #c8ccd0;border-radius:11px;'
                f'font-size:11px;color:#1f2329"><b>컨텍스트:</b> '
                f'{escape(ctx_label)}</span>'
            ),
        ]

        sugs = get_suggestions(text, self.tables)
        if not sugs:
            children.append(W.HTML(
                '<span style="color:#888;font-size:11px;font-style:italic;'
                'padding:4px">(추천 없음)</span>'
            ))
        else:
            kind_color = {
                "table": "#047857",
                "column": "#b45309",
                "keyword": "#2563eb",
                "function": "#7c3aed",
                "star": "#000000",
            }
            for s in sugs[:15]:
                color = kind_color.get(s["kind"], "#1f2329")
                btn = W.Button(
                    description=s["label"],
                    tooltip=s.get("meta", "") or s["kind"],
                    layout=W.Layout(margin="2px", width="auto", height="22px"),
                )
                btn.style.button_color = "#ffffff"
                btn.style.text_color = color
                btn.on_click(self._make_inserter(s["value"]))
                children.append(btn)

        self._suggest_box.children = children

    def _on_run(self, _btn: Any) -> None:
        from IPython.display import display
        sql = self._textarea.value
        with self._output:
            self._output.clear_output()
            if not sql.strip():
                print("⚠ SQL 이 비어있습니다.")
                return
            if self.on_execute is None:
                print("on_execute 콜백이 등록되지 않았습니다.")
                print("SQLRunner(on_execute=lambda sql: pd.read_sql(sql, conn))")
                print("처럼 콜백을 주입하면 ▶ 실행 시 호출됩니다.\n")
                print(f"SQL:\n{sql}")
                return
            try:
                result = self.on_execute(sql)
                if result is not None:
                    display(result)
                else:
                    print("✓ 실행 완료 (반환값 없음)")
            except Exception as e:
                import traceback
                print(f"❌ {type(e).__name__}: {e}")
                traceback.print_exc()

    def _on_copy(self, _btn: Any) -> None:
        # ipywidgets 만으로는 직접 클립보드 접근이 어려워 JS 한 조각 display
        from IPython.display import display, HTML
        # JS template literal 이라 백틱과 백슬래시만 안전 escape
        sql_js = (
            self._textarea.value
            .replace("\\", "\\\\")
            .replace("`", "\\`")
            .replace("$", "\\$")
        )
        with self._output:
            self._output.clear_output()
            display(HTML(
                "<script>(function(){"
                "const t=`" + sql_js + "`;"
                "if(navigator.clipboard&&navigator.clipboard.writeText){"
                "navigator.clipboard.writeText(t).then("
                "()=>{},()=>{alert('clipboard 차단 — 수동 복사가 필요합니다');}"
                ");}else{alert('clipboard API 미지원');}"
                "})();</script>"
                '<div style="padding:4px 8px;color:#047857;font-size:12px">'
                '✓ 클립보드에 복사 시도됨</div>'
            ))

    def _on_clear(self, _btn: Any) -> None:
        if self._textarea is not None:
            self._textarea.value = ""


# ===== 5. __main__ =====

if __name__ == "__main__":
    print(
        "이 파일은 라이브러리입니다. 사용 예시는 examples/basic_usage.py 또는\n"
        "examples/demo.ipynb 를 참고하세요."
    )
