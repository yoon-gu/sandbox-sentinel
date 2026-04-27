"""
SQL Runner with CodeMirror inline (single-file, 폐쇄망 친화).

005 / 006 변환물 비교
---------------------
  · 005 = **CodeMirror 5.65.16 인라인 임베드** (이 파일) — Jupyter 셀
          안에서 에디터 자체에 syntax highlight 색 + popup 자동완성.
          ▶ 실행 버튼으로 Python 콜백 호출.
  · 006 = 터미널 풀스크린 (Textual TUI) — 노트북/브라우저 불필요, ssh 친화.

포지셔닝 한 줄: "노트북 안에서 진짜 IDE 같은 SQL 편집 체감 + ▶ 실행 콜백"

라이선스: MIT (CodeMirror) + MIT (오리지널 wrapper)
생성: Code Conversion Agent

핵심 기능
--------
  1) 좌측 entity 트리 — add_table / from_dict / from_sqlite /
     from_dataframes 스키마 API, 클릭 시 에디터 커서 위치에 정확히 인서트
  2) 우측 CodeMirror 에디터 — SQL syntax highlight, line number, dracula
     dark theme. Ctrl+Space → 컨텍스트 인식 자동완성 popup
  3) 컨텍스트 인식 자동완성 — 005 의 anchor 정책을 JS 사이드로 그대로 재현.
     `FROM`/`JOIN` 다음 → 테이블, `SELECT` 다음 → 컬럼+`*`+함수, `table.`
     입력 시 → 해당 테이블 컬럼만 등.
  4) ▶ 실행 (Cmd/Ctrl+Enter) → `on_execute(sql)` Python 콜백 호출, 반환값을
     Output 위젯에 display (DataFrame 도 그대로 표 렌더)
  5) 외부 네트워크 / CDN / 바이너리 영속화 일절 없음 — single-file 반입

사용 예시
--------
    from sql_codemirror import SQLRunnerCM
    runner = SQLRunnerCM.with_sqlite("./demo.db")    # thread-safe 헬퍼
    runner.set_query("SELECT * FROM users LIMIT 10;")
    runner.show()

또는

    import pandas as pd, sqlite3
    runner = SQLRunnerCM(on_execute=lambda sql: pd.read_sql(sql, conn))
    runner.from_sqlite("./demo.db").show()
"""
from __future__ import annotations

import datetime
import json
import re
import sqlite3
import uuid
from html import escape
from typing import Any, Callable, Iterable, Mapping, Optional, Union


# %%BUNDLE%%


# ===== 타입 alias =====

ColumnSpec = Union[str, tuple, Mapping[str, Any]]


# ===== SQL 키워드 / 함수 (JS 쪽 자동완성 정책과 공유) =====
# 005 와 동일 세트 — 변경 시 양쪽 동기화 필요

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


# ===== 컨텍스트 감지 + 추천 (Python 사이드 — 005 와 동일 골격) =====
# CM 안의 popup 자동완성은 JS 사이드에서 contextHint() 가 처리.
# 여기 Python 함수들은 에디터 아래에 늘 띄워두는 칩 패널 (=005 의 추천
# 영역) 을 ipywidgets.Button 으로 그릴 때 사용한다. JS 가 cursorActivity
# 마다 'before-cursor' 텍스트를 hidden Textarea 로 sync 하므로 이 함수는
# 그 텍스트를 받아 context 를 분석한다.

_ANCHORS = {
    "SELECT", "FROM", "WHERE", "JOIN", "ON", "AND", "OR",
    "GROUP", "ORDER", "HAVING", "LIMIT", "BY",
    "INSERT", "UPDATE", "DELETE", "SET", "INTO", "VALUES",
    "INNER", "LEFT", "RIGHT", "FULL",
    "UNION", "EXCEPT", "INTERSECT",
    "AS", "WITH",
}


def detect_context(text: str) -> str:
    """직전 anchor 키워드로 추천 종류를 결정.

    weak anchor (`AS` / `WITH` / `VALUES`) 는 콤마를 지나친 뒤에는
    건너뛰고 더 깊은 clause anchor(SELECT 등)를 찾는다. 그래야
    `SELECT col AS alias, |` 처럼 새 항목 시작 위치에서 컬럼 추천이 뜸.
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
            # weak anchor 는 콤마를 지나친 뒤에는 건너뜀 (그 AS 는 이전
            # 항목에 묶인 것이고, 사용자는 새 항목을 시작 중)
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
        "LIMIT": "number",
        "DELETE": "from_keyword",
        "VALUES": "any", "AS": "any", "WITH": "any",
    }
    return MAP.get(last, "general")


# alias 위치에 와도 alias 가 아닌 reserved keyword 들
_NOT_ALIAS = {
    "WHERE", "ON", "GROUP", "ORDER", "HAVING", "LIMIT", "JOIN",
    "INNER", "LEFT", "RIGHT", "FULL", "OUTER", "CROSS", "UNION",
    "EXCEPT", "INTERSECT", "AS", "USING", "SET", "VALUES",
}
# FROM clause 끝을 알리는 키워드 (이 키워드가 나오면 더 이상 콤마 list 안 봄)
_CLAUSE_END_RE = re.compile(
    r"\b(?:WHERE|GROUP|ORDER|HAVING|LIMIT|JOIN|INNER|LEFT|RIGHT|FULL"
    r"|OUTER|CROSS|UNION|EXCEPT|INTERSECT|ON|USING)\b",
    re.IGNORECASE,
)
# 한 항목 ('schema.table AS alias' / 'table alias' / 'table' 모두 허용)
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
    """``FROM <t> [AS] <alias>``, ``JOIN <t> [AS] <alias>`` 스캔.

    지원하는 패턴:
      · ``FROM orders``, ``FROM orders o``, ``FROM orders AS o``
      · ``FROM orders o, users u`` (콤마 join — 두 번째 이후도 인식)
      · ``FROM public.orders AS o`` (schema-qualified — 마지막 segment 만)
      · ``JOIN events AS e``, ``JOIN public.events e`` (스키마 포함)
    본명도 자기 자신에 매핑되어 'orders.' / 'o.' 둘 다 동작.
    """
    s = re.sub(r"--[^\n]*", " ", text)
    s = re.sub(r"/\*.*?\*/", " ", s, flags=re.DOTALL)
    s = re.sub(r"'[^']*'", " ", s)
    s = re.sub(r'"[^"]*"', " ", s)
    aliases: dict = {}

    def _register(tname_full: str, alias: Optional[str]) -> None:
        # schema-qualified 면 마지막 segment 사용
        tname = tname_full.split(".")[-1]
        if tname not in tables:
            return
        aliases[tname] = tname
        if alias and alias.upper() not in _NOT_ALIAS:
            aliases[alias] = tname

    # FROM clause — 다음 절 키워드 전까지 잘라 콤마 list 처리
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

    # JOIN — 단일 테이블 (콤마 list 아님)
    for m in _JOIN_RE.finditer(s):
        _register(m.group(1), m.group(2))

    return aliases


def get_suggestions(text: str, tables: Mapping[str, list],
                     full_text: Optional[str] = None) -> list:
    """현재 컨텍스트에 맞는 추천 후보 리스트 (텍스트 끝 기준).

    Args:
        text: cursor 까지의 텍스트 (컨텍스트 감지에 사용).
        tables: 스키마 매핑.
        full_text: 전체 SQL 문서. alias 추출에 사용. None 이면 ``text``.
            SELECT 절(FROM 보다 앞)에서도 'o.' / 'e.' 가 동작하려면
            반드시 전체 텍스트를 넘겨야 함.
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
        # 본명/AS alias 모두 매핑. alias 추출은 전체 문서 기준 — SELECT 절
        # 처럼 FROM 보다 앞에 있을 때도 뒤쪽 FROM/JOIN 을 보고 매핑해야 함.
        aliases = extract_aliases(full_text if full_text is not None else text, tables)
        real = aliases.get(qual)
        if real and real in tables:
            return [
                {
                    "value": f"{qual}.{c['name']}",   # 사용자가 친 그대로 인서트
                    "label": (f"{c['name']} {_short_type(c.get('type',''))}"
                              if c.get("type") else c["name"]),
                    "kind": "column",
                    "meta": c.get("type", "") or real,
                }
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
                # 추천 표시 라벨에 짧은 타입 이모지 동시 노출 ("id 🔢")
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

    # 항상 KEYWORDS / FUNCTIONS fallback (JS 사이드와 정책 일치)
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


# ===== SQL 타입명 → 짧은 이모지 매핑 =====
# 추천 표시할 때 'id (INTEGER)' 처럼 길게 나오는 게 산만해서, 대표 이모지
# 한 글자로 단축. 알 수 없는 타입은 첫 글자만 사용.
# 적용 위치: Python get_suggestions (chip 추천) + JS contextHint (popup).

def _short_type(t: str) -> str:
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


# ===== 컬럼 스펙 정규화 =====

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


# ===== SQL 문법 검증 (Python 사이드 · 외부 의존 없음) =====
# 폐쇄망 호환 — sqlparse / sqlglot 등 별도 패키지 없이 동작. 깊은 grammar
# 검증이 필요하면 SQLRunnerCM(on_validate=fn) 으로 사용자 정의 콜백 주입.

_VALID_SQL_STARTS = {
    "SELECT", "WITH", "INSERT", "UPDATE", "DELETE",
    "CREATE", "DROP", "ALTER", "TRUNCATE",
    "EXPLAIN", "PRAGMA", "REPLACE", "VACUUM",
    "ATTACH", "DETACH", "ANALYZE",
    "BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE",
    "SHOW", "DESCRIBE", "DESC", "USE",
}


def _strip_sql_comments(sql: str) -> str:
    """검증 전에 -- 라인 주석과 /* */ 블록 주석을 제거."""
    sql = re.sub(r"--[^\n]*", "", sql)
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    return sql


def _is_balanced_quotes(sql: str) -> tuple[bool, str]:
    """문자열 리터럴 (', ") 이 짝이 맞는지 검사. 라인 주석 제거 후 호출."""
    in_single = False
    in_double = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        if in_single:
            if ch == "'":
                # SQL 표준: '' 는 escape — 두 개 연속이면 그대로
                if i + 1 < len(sql) and sql[i + 1] == "'":
                    i += 2
                    continue
                in_single = False
        elif in_double:
            if ch == '"':
                if i + 1 < len(sql) and sql[i + 1] == '"':
                    i += 2
                    continue
                in_double = False
        else:
            if ch == "'":
                in_single = True
            elif ch == '"':
                in_double = True
        i += 1
    if in_single:
        return False, "작은따옴표(')가 닫히지 않았습니다"
    if in_double:
        return False, "큰따옴표(\")가 닫히지 않았습니다"
    return True, ""


def validate_sql(sql: str) -> tuple[bool, Optional[str]]:
    """SQL 의 기본 문법을 검사 (외부 의존 없음).

    Returns:
        (ok, message). ok=True 면 message=None. ok=False 면 사용자에게
        보여줄 한글 한 줄 메시지.

    검사 항목 (모두 통과해야 ok=True):
      1. 빈 문자열 / 주석만 있음 → False
      2. 첫 비공백 토큰이 알려진 SQL verb (SELECT, WITH, INSERT, …) → True
      3. 괄호 ( ) 가 짝 → True
      4. 따옴표 ', " 가 닫힘 → True

    grammar 수준 (테이블/컬럼 존재) 은 검증하지 않으며 on_execute 시점의
    예외로 사용자에게 전달됨.
    """
    if not sql or not sql.strip():
        return False, "SQL 이 비어있습니다"

    cleaned = _strip_sql_comments(sql).strip()
    if not cleaned:
        return False, "SQL 이 주석만 있습니다"

    # 첫 토큰 검사
    m = re.match(r"\s*(\w+)", cleaned)
    if not m:
        return False, "SQL 시작 키워드를 찾을 수 없습니다"
    first = m.group(1).upper()
    if first not in _VALID_SQL_STARTS:
        return False, (
            f"알 수 없는 SQL 시작 키워드: '{m.group(1)}' "
            f"(허용: SELECT · WITH · INSERT · UPDATE · DELETE 등)"
        )

    # 따옴표 균형 검사 — 라인 주석을 제거한 텍스트에서
    ok_q, msg_q = _is_balanced_quotes(cleaned)
    if not ok_q:
        return False, msg_q

    # 괄호 균형 검사 — 따옴표 안의 ( ) 는 무시해야 정확
    depth = 0
    in_single = in_double = False
    i = 0
    while i < len(cleaned):
        ch = cleaned[i]
        if in_single:
            if ch == "'":
                if i + 1 < len(cleaned) and cleaned[i + 1] == "'":
                    i += 2
                    continue
                in_single = False
        elif in_double:
            if ch == '"':
                if i + 1 < len(cleaned) and cleaned[i + 1] == '"':
                    i += 2
                    continue
                in_double = False
        else:
            if ch == "'":
                in_single = True
            elif ch == '"':
                in_double = True
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth < 0:
                    return False, "닫는 괄호 ')' 가 여는 괄호보다 많습니다"
        i += 1
    if depth > 0:
        return False, f"여는 괄호 '(' 가 {depth} 개 닫히지 않았습니다"

    return True, None


# ===== 실행 history 뷰 (list 인 동시에 callable) =====
# runner.history          → list 처럼 인덱싱 / iteration / append
# runner.history()        → 보기 좋은 HTML 표시 + SQL/전체 복사 버튼

class _HistoryView(list):
    """list 를 상속한 실행 이력 컨테이너.

    `runner.history` 는 list 로 동작 (`runner.history[-1]`, `len(...)`,
    `for entry in runner.history` 등). `runner.history()` 호출 시 노트북에
    HTML 로 보기 좋게 표시되며, 각 항목별 [📋 SQL 복사] 버튼과 상단의
    [📋 전체 history 복사] 버튼이 포함된다.

    각 entry 의 dict 형식:
        {"timestamp": "2026-04-27 15:42:11",
         "query":     "SELECT ...",
         "result":    <DataFrame | list | dict | None>,
         "error":     None | Exception}
    """

    def __call__(self, n: Optional[int] = None,
                 full: bool = False) -> None:
        try:
            from IPython.display import display, HTML
        except ImportError:
            for entry in (list(self) if n is None else list(self)[-n:]):
                ts = entry.get("timestamp", "")
                err = entry.get("error")
                status = "❌" if err else "✓"
                print(f"[{ts}] {status}")
                print(entry["query"])
                if err:
                    print(f"   → {type(err).__name__}: {err}")
                print()
            return
        entries = list(self) if n is None else list(self)[-n:]
        display(HTML(_render_history_html(entries, full=full)))

    def to_markdown(self) -> str:
        """전체 history 를 markdown 텍스트로 직렬화 (외부 사용)."""
        return _history_to_markdown(list(self))


def _history_to_markdown(entries: list) -> str:
    """history entry list 를 markdown 텍스트로 직렬화.

    각 entry 는 timestamp, status, SQL, 에러(있으면) 를 포함한 절로 변환.
    """
    if not entries:
        return "_(history 비어있음)_\n"
    lines = [f"# SQL 실행 history ({len(entries)} 건)\n"]
    for i, entry in enumerate(entries, 1):
        ts = entry.get("timestamp", "")
        err = entry.get("error")
        status = "❌ 에러" if err else "✓ 성공"
        lines.append(f"## #{i} · {ts} · {status}\n")
        lines.append("```sql")
        lines.append(entry["query"].rstrip())
        lines.append("```\n")
        if err:
            lines.append(f"**에러**: `{type(err).__name__}: {err}`\n")
    return "\n".join(lines)


def _result_preview_html(entry: dict, max_rows: int = 5) -> str:
    """history HTML 렌더용 — 결과 객체를 짧게 미리보기.

    파일에서 로드된 entry (``from_file=True``) 는 result 본체가 없으므로
    row_count/col_count 메타만 표시.
    """
    result = entry.get("result")
    if entry.get("from_file"):
        rc = entry.get("row_count")
        cc = entry.get("col_count")
        if rc is None and cc is None:
            return ("<div class='hist-result-none'>"
                    "이전 세션 결과 — 파일에 메타만 보존됨</div>")
        meta = []
        if rc is not None:
            meta.append(f"{rc}행")
        if cc is not None:
            meta.append(f"{cc}컬럼")
        return (f"<div class='hist-result-none'>"
                f"이전 세션 결과 ({' × '.join(meta)})</div>")
    if result is None:
        return "<div class='hist-result-none'>✓ 완료 (반환값 없음)</div>"
    try:
        import pandas as pd
        if isinstance(result, pd.DataFrame):
            preview = result.head(max_rows)
            n_total = len(result)
            n_cols = len(result.columns)
            html = preview.to_html(index=False, border=0,
                                   classes="hist-df")
            more = (f" (전체 {n_total}행 × {n_cols}컬럼)"
                    if n_total > max_rows
                    else f" ({n_total}행 × {n_cols}컬럼)")
            return f"{html}<div class='hist-result-meta'>{more}</div>"
    except ImportError:
        pass
    s = repr(result)
    if len(s) > 400:
        s = s[:400] + " …"
    return f"<pre class='hist-result-other'>{escape(s)}</pre>"


def _group_by_date(entries: list) -> list:
    """entries 를 날짜(YYYY-MM-DD)별로 묶어 (date, [entries…]) 리스트 반환.
    최신 날짜가 앞, 같은 날짜 안에서는 timestamp 오름차순."""
    from collections import OrderedDict
    buckets: dict = OrderedDict()
    for e in entries:
        ts = e.get("timestamp", "") or ""
        date = ts.split(" ")[0] if " " in ts else (ts or "(미상)")
        buckets.setdefault(date, []).append(e)
    sorted_dates = sorted(buckets.keys(), reverse=True)
    return [(d, buckets[d]) for d in sorted_dates]


def _render_history_html(entries: list, full: bool = True) -> str:
    """history HTML 렌더 — 좌측 날짜 sidebar + 항목별 [📋 SQL 복사].

    날짜를 클릭하면 해당 날짜 entry 만 표시 (JS 만으로 처리). 클립보드는
    navigator.clipboard 우선, 권한 차단 시 textarea + execCommand 폴백.
    """
    if not entries:
        return ("<div class='sql-history-view empty'>"
                "<i>(history 비어있음 — ▶ 실행을 누르면 누적됩니다)</i></div>")
    view_uid = "hv-" + uuid.uuid4().hex[:8]
    md_js = json.dumps(_history_to_markdown(entries))
    grouped = _group_by_date(entries)
    selected = grouped[0][0]   # 최신 날짜 기본 선택

    # ── CSS ──
    css = (
        "<style>"
        ".sql-history-view{font-family:ui-sans-serif,system-ui;"
        " font-size:12px;color:#1f2329;}"
        ".sql-history-view .hv-top{padding:6px 0;display:flex;"
        " gap:8px;align-items:center;}"
        ".sql-history-view .hv-body{display:flex;gap:12px;"
        " min-height:280px;}"
        ".sql-history-view .hv-sidebar{width:170px;flex-shrink:0;"
        " border:1px solid #d8dde1;border-radius:4px;background:#f7f8fa;"
        " max-height:560px;overflow-y:auto;}"
        ".sql-history-view .hv-sidebar .sb-header{padding:6px 10px;"
        " font-weight:600;border-bottom:1px solid #e1e4e7;"
        " background:#eef0f3;}"
        ".sql-history-view .date-item{padding:8px 10px;cursor:pointer;"
        " border-bottom:1px solid #e1e4e7;transition:background .12s;}"
        ".sql-history-view .date-item:hover{background:#eef0f3;}"
        ".sql-history-view .date-item.active{"
        " background:#2563eb;color:#fff;}"
        ".sql-history-view .date-item.active .c{color:#dbeafe;}"
        ".sql-history-view .date-item .d{font-weight:600;}"
        ".sql-history-view .date-item .c{font-size:10px;color:#6c757d;"
        " margin-top:2px;}"
        ".sql-history-view .hv-main{flex:1;min-width:0;"
        " max-height:560px;overflow-y:auto;padding-right:4px;}"
        ".sql-history-view .hist-group{display:none;}"
        ".sql-history-view .hist-group.visible{display:block;}"
        ".sql-history-view .hist-entry{background:#f7f8fa;"
        " border:1px solid #d8dde1;border-radius:4px;"
        " padding:8px 10px;margin:6px 0;}"
        ".sql-history-view .hist-entry.err{"
        " background:#fff4f4;border-color:#f1a4a4;}"
        ".sql-history-view .hist-entry.from-file{"
        " border-left:3px solid #94a3b8;}"
        ".sql-history-view .hist-meta{display:flex;align-items:center;"
        " gap:8px;margin-bottom:4px;flex-wrap:wrap;}"
        ".sql-history-view .ts{color:#6c757d;font-size:11px;}"
        ".sql-history-view .badge{font-size:10px;padding:1px 6px;"
        " border-radius:8px;background:#e2e8f0;color:#475569;}"
        ".sql-history-view pre.hist-sql{background:#fff;"
        " border:1px solid #c8ccd0;border-radius:3px;padding:6px 8px;"
        " margin:4px 0;font-size:12px;overflow-x:auto;"
        " white-space:pre-wrap;}"
        ".sql-history-view button.copy{cursor:pointer;background:#fff;"
        " border:1px solid #c8ccd0;border-radius:3px;font-size:11px;"
        " padding:1px 6px;}"
        ".sql-history-view button.copy:hover{background:#eef0f3;}"
        ".sql-history-view button.copy.ok{background:#d1fae5;}"
        ".sql-history-view .hist-err{color:#b91c1c;font-size:11px;"
        " margin-top:4px;}"
        ".sql-history-view .hist-result-none{color:#6c757d;"
        " font-style:italic;}"
        ".sql-history-view .hist-result-meta{color:#6c757d;"
        " font-size:11px;margin-top:2px;}"
        ".sql-history-view table.hist-df{border-collapse:collapse;"
        " margin:4px 0;font-size:11px;}"
        ".sql-history-view table.hist-df th,"
        ".sql-history-view table.hist-df td{"
        " border:1px solid #e1e4e7;padding:2px 6px;}"
        ".sql-history-view table.hist-df th{background:#eef0f3;}"
        "</style>"
    )

    # ── sidebar (날짜 리스트) ──
    sidebar_items = ["<div class='sb-header'>📅 날짜별</div>"]
    for date, group in grouped:
        cnt = len(group)
        ok = sum(1 for e in group if e.get("error") is None)
        err = cnt - ok
        active = " active" if date == selected else ""
        meta = f"{cnt}건"
        if err:
            meta += f" ({ok}✓ {err}❌)"
        sidebar_items.append(
            f"<div class='date-item{active}' data-date='{escape(date)}'>"
            f"<div class='d'>{escape(date)}</div>"
            f"<div class='c'>{meta}</div></div>"
        )

    # ── main (날짜별 entry 그룹) ──
    group_divs = []
    for date, group in grouped:
        vis = " visible" if date == selected else ""
        rows = []
        for i, entry in enumerate(group, 1):
            ts = entry.get("timestamp", "") or ""
            sql = entry["query"]
            err = entry.get("error")
            from_file = entry.get("from_file", False)
            cls = "hist-entry"
            if err:
                cls += " err"
            if from_file:
                cls += " from-file"
            status = "❌" if err else "✓"
            badges = []
            if from_file:
                badges.append("<span class='badge'>이전 세션</span>")
            if isinstance(err, SyntaxError):
                badges.append("<span class='badge'>문법 검증 실패</span>")
            rows.append(f"<div class='{cls}'>")
            rows.append(
                "<div class='hist-meta'>"
                f"<b>{status} #{i}</b>"
                f"<span class='ts'>{escape(ts)}</span>"
                + "".join(badges)
                + "<button class='copy copy-sql'>📋 SQL 복사</button>"
                "</div>"
            )
            rows.append(f"<pre class='hist-sql'>{escape(sql)}</pre>")
            if err is not None:
                rows.append(
                    f"<div class='hist-err'>❌ "
                    f"<b>{escape(type(err).__name__)}</b>: "
                    f"{escape(str(err))}</div>"
                )
            elif full:
                rows.append(_result_preview_html(entry))
            rows.append("</div>")
        group_divs.append(
            f"<div class='hist-group{vis}' data-date='{escape(date)}'>"
            + "".join(rows) + "</div>"
        )

    head = (
        f"<div class='sql-history-view' id='{view_uid}'>"
        f"<div class='hv-top'>"
        f"<b>SQL 실행 history</b> "
        f"<span style='color:#6c757d'>"
        f"({len(entries)} 건 · {len(grouped)} 일)</span>"
        f"<button class='copy copy-all'>📋 전체 history 복사 (markdown)</button>"
        f"</div>"
        f"<div class='hv-body'>"
        f"<div class='hv-sidebar'>{''.join(sidebar_items)}</div>"
        f"<div class='hv-main'>{''.join(group_divs)}</div>"
        f"</div>"
        f"</div>"
    )

    script = (
        "<script>(function(){"
        f"const root=document.getElementById('{view_uid}');"
        "if(!root)return;"
        # 날짜 클릭 → 해당 그룹만 visible
        "root.querySelectorAll('.date-item').forEach(it=>{"
        " it.addEventListener('click',()=>{"
        "  const d=it.dataset.date;"
        "  root.querySelectorAll('.date-item').forEach(x=>"
        "   x.classList.toggle('active',x.dataset.date===d));"
        "  root.querySelectorAll('.hist-group').forEach(g=>"
        "   g.classList.toggle('visible',g.dataset.date===d));"
        " });});"
        # 클립보드 복사 (navigator.clipboard + 폴백)
        "function fb(text,btn){"
        " const ta=document.createElement('textarea');"
        " ta.value=text;ta.style.position='fixed';ta.style.opacity='0';"
        " document.body.appendChild(ta);ta.select();"
        " try{document.execCommand('copy');btn.textContent='✓ 복사됨';"
        "  btn.classList.add('ok');"
        "  setTimeout(()=>{btn.textContent=btn.dataset.orig;"
        "   btn.classList.remove('ok');},1500);}"
        " catch(e){alert('복사 차단됨. 직접 선택해 복사하세요.');}"
        " document.body.removeChild(ta);}"
        "function copy(text,btn){"
        " btn.dataset.orig=btn.dataset.orig||btn.textContent;"
        " if(navigator.clipboard&&window.isSecureContext){"
        "  navigator.clipboard.writeText(text).then("
        "   ()=>{btn.textContent='✓ 복사됨';btn.classList.add('ok');"
        "    setTimeout(()=>{btn.textContent=btn.dataset.orig;"
        "     btn.classList.remove('ok');},1500);},"
        "   ()=>fb(text,btn));"
        " }else{fb(text,btn);}}"
        "root.querySelectorAll('.copy-sql').forEach(btn=>{"
        " btn.addEventListener('click',()=>{"
        "  const pre=btn.closest('.hist-entry').querySelector('pre.hist-sql');"
        "  copy(pre.textContent,btn);});});"
        f"const allMd={md_js};"
        "const allBtn=root.querySelector('.copy-all');"
        "if(allBtn){allBtn.addEventListener('click',"
        " ()=>copy(allMd,allBtn));}"
        "})();</script>"
    )

    return css + head + script


# ===== 에디터 부트스트랩 JS (CM 인스턴스 생성 + 컨텍스트 자동완성) =====
# 이 문자열은 .format() 으로 placeholder 치환 후 <script> 안에 삽입됨.
# 중괄호는 모두 `{{` `}}` 로 escape.

_BOOTSTRAP_JS_TPL = r"""
(function(){{
  var UID = "{uid}";
  var SCHEMA = {schema_json};
  var KEYWORDS = {keywords_json};
  var FUNCTIONS = {functions_json};

  // ipywidgets 의 hidden Textarea (전체 SQL · 커서까지) 와 mount div 를 찾아
  // mount. ipywidgets 가 layout 을 비동기로 그릴 수 있어 폴링.
  function tryMount(){{
    var mount = document.getElementById("cm-mount-" + UID);
    var taWrap = document.querySelector(".cm-ta-" + UID);
    var curWrap = document.querySelector(".cm-cursor-" + UID);
    if(!mount || !taWrap || !curWrap) return false;
    var ta  = taWrap.querySelector("textarea");
    var cur = curWrap.querySelector("textarea");
    if(!ta || !cur) return false;
    if(mount.dataset.mounted === "1") return true;
    mount.dataset.mounted = "1";
    initCM(mount, ta, cur);
    return true;
  }}
  if(!tryMount()){{
    var tries = 0;
    var iv = setInterval(function(){{
      tries++;
      if(tryMount() || tries > 80){{ clearInterval(iv); }}
    }}, 50);
  }}

  function initCM(mount, ta, curTa){{
    if(typeof CodeMirror === "undefined"){{
      mount.innerHTML = '<div style="color:#a00;padding:8px">'+
        'CodeMirror 로드 실패 — Jupyter 노트북이 trusted 상태인지 확인하세요. '+
        '(File → Trust Notebook)</div>';
      return;
    }}
    // hidden textarea 들은 보이지 않게 하되 ipywidgets 의 sync 는 살림
    var taWrap  = ta.closest(".cm-ta-" + UID);
    var curWrap = curTa.closest(".cm-cursor-" + UID);
    if(taWrap)  {{ taWrap.style.display  = "none"; }}
    if(curWrap) {{ curWrap.style.display = "none"; }}

    var cm = CodeMirror(mount, {{
      value: ta.value,
      mode: "text/x-sql",
      theme: "dracula",
      lineNumbers: true,
      lineWrapping: true,
      indentUnit: 2,
      tabSize: 2,
      smartIndent: true,
      matchBrackets: true,
      autofocus: false,
      hintOptions: {{
        hint: contextHint,
        completeSingle: false,
        closeOnUnfocus: true,
      }},
      extraKeys: {{
        "Ctrl-Space": "autocomplete",
        "Cmd-Space":  "autocomplete",
        "Cmd-Enter":  function(){{ triggerRun(); }},
        "Ctrl-Enter": function(){{ triggerRun(); }},
        "Tab": function(cm){{
          if(cm.somethingSelected()){{ cm.indentSelection("add"); }}
          else {{ cm.replaceSelection(Array(cm.getOption("indentUnit")+1).join(" "), "end", "+input"); }}
        }},
      }},
    }});
    cm.setSize("100%", 600);   // 약 30 줄 표시

    // CM → hidden textarea 동기화. ta 는 전체 SQL, curTa 는 시작부터 커서
    // 까지의 텍스트. Python 의 _update_suggest 는 curTa 를 observe 하여
    // 커서가 화살표로 이동만 해도 컨텍스트 추천이 갱신됨.
    function syncCursor(){{
      var doc = cm.getDoc();
      var before = doc.getRange({{line:0, ch:0}}, doc.getCursor());
      curTa.value = before;
      curTa.dispatchEvent(new Event("input",  {{ bubbles: true }}));
      curTa.dispatchEvent(new Event("change", {{ bubbles: true }}));
    }}
    cm.on("change", function(){{
      ta.value = cm.getValue();
      ta.dispatchEvent(new Event("input",  {{ bubbles: true }}));
      ta.dispatchEvent(new Event("change", {{ bubbles: true }}));
      syncCursor();
    }});
    cm.on("cursorActivity", syncCursor);
    // 초기 1회
    syncCursor();

    // 식별자 입력 중일 때 자동 popup. 중간-텍스트 타이핑에서도 안정적으로
    // 뜨도록 setTimeout 으로 cursorActivity 경합을 피하고, +input/paste 만
    // 트리거. 마지막 줄의 끝 글자 1자가 word 문자면 발화.
    cm.on("inputRead", function(cm, change){{
      if(!change) return;
      if(change.origin !== "+input" && change.origin !== "paste") return;
      var lines = change.text || [];
      if(lines.length === 0) return;
      var lastLine = lines[lines.length - 1] || "";
      if(lastLine.length === 0) return;
      var lastCh = lastLine[lastLine.length - 1];
      if(!/[A-Za-z0-9_.]/.test(lastCh)) return;
      // 다음 tick 으로 미뤄 cursorActivity / change 처리 후 안정 상태에서
      // showHint. 이미 popup 이 떠 있으면 건너뜀 (중복 방지).
      setTimeout(function(){{
        if(cm.state && cm.state.completionActive) return;
        cm.showHint({{ hint: contextHint, completeSingle: false }});
      }}, 10);
    }});

    // entity 트리 클릭 → CM 커서 위치에 인서트
    window["__cmInsert_" + UID] = function(snippet){{
      var doc = cm.getDoc();
      var cur = doc.getCursor();
      // 마지막 부분 단어가 snippet 의 prefix 이면 치환, 아니면 그냥 삽입
      var line = doc.getLine(cur.line);
      var i = cur.ch;
      while(i > 0 && /[\w_.]/.test(line[i-1])) i--;
      var lastWord = line.substring(i, cur.ch);
      if(lastWord && snippet.toLowerCase().indexOf(lastWord.toLowerCase()) === 0){{
        doc.replaceRange(snippet, {{line: cur.line, ch: i}}, cur);
      }} else {{
        var sep = "";
        if(cur.ch > 0){{
          var prev = line[cur.ch-1];
          if(prev && !/[\s(,.]/.test(prev)) sep = " ";
        }}
        doc.replaceRange(sep + snippet, cur);
      }}
      cm.focus();
    }};

    // ▶ 실행 외부 호출 hook
    function triggerRun(){{
      var btn = document.querySelector(".cm-run-" + UID + " button");
      if(btn){{ btn.click(); }}
    }}
    window["__cmRun_" + UID] = triggerRun;
    window["__cmEditor_" + UID] = cm;
  }}

  // FROM/JOIN <table> [AS] <alias> 를 스캔해 alias → 실 테이블 매핑.
  // Python extract_aliases 와 동일 정책. 콤마 join + schema-qualified 지원.
  var NOT_ALIAS = {{
    "WHERE":1,"ON":1,"GROUP":1,"ORDER":1,"HAVING":1,"LIMIT":1,"JOIN":1,
    "INNER":1,"LEFT":1,"RIGHT":1,"FULL":1,"OUTER":1,"CROSS":1,
    "UNION":1,"EXCEPT":1,"INTERSECT":1,"AS":1,"USING":1,"SET":1,"VALUES":1
  }};
  var CLAUSE_END_RE = /\b(?:WHERE|GROUP|ORDER|HAVING|LIMIT|JOIN|INNER|LEFT|RIGHT|FULL|OUTER|CROSS|UNION|EXCEPT|INTERSECT|ON|USING)\b/i;
  var TABLE_REF_RE = /^\s*(\w+(?:\.\w+)?)\s*(?:(?:AS\s+)?(\w+))?\s*$/i;

  function extractAliases(text){{
    var s = text
      .replace(/--[^\n]*/g," ")
      .replace(/\/\*[\s\S]*?\*\//g," ")
      .replace(/'[^']*'/g," ")
      .replace(/"[^"]*"/g," ");
    var aliases = {{}};

    function register(tnameFull, alias){{
      var parts = tnameFull.split(".");
      var tname = parts[parts.length - 1];   // schema-qualified → 마지막
      if(!SCHEMA[tname]) return;
      aliases[tname] = tname;
      if(alias && !NOT_ALIAS[alias.toUpperCase()]){{
        aliases[alias] = tname;
      }}
    }}

    // FROM clause — 다음 절 키워드 전까지 잘라 콤마 list
    var fromRe = /\bFROM\b/gi;
    var fm;
    while((fm = fromRe.exec(s)) !== null){{
      var rest = s.substring(fromRe.lastIndex);
      var em = rest.match(CLAUSE_END_RE);
      var fromClause = em ? rest.substring(0, em.index) : rest;
      var parts = fromClause.split(",");
      for(var i = 0; i < parts.length; i++){{
        var part = parts[i].replace(/^[\s]+|[\s;]+$/g, "");
        if(!part) continue;
        var tm = part.match(TABLE_REF_RE);
        if(!tm) continue;
        register(tm[1], tm[2]);
      }}
    }}

    // JOIN — 단일 테이블
    var joinRe = /\bJOIN\s+(\w+(?:\.\w+)?)(?:\s+(?:AS\s+)?(\w+))?/gi;
    var jm;
    while((jm = joinRe.exec(s)) !== null){{
      register(jm[1], jm[2]);
    }}

    return aliases;
  }}

  // SQL 타입명을 짧은 이모지로 단축. Python _short_type 과 동일 매핑.
  function shortType(t){{
    if(!t) return "";
    var u = t.toUpperCase();
    if(u.indexOf("INT")>=0 || u.indexOf("SERIAL")>=0) return "🔢";
    if(/(REAL|FLOAT|DOUBLE|NUMERIC|DECIMAL|MONEY)/.test(u)) return "📊";
    if(/(CHAR|TEXT|STRING|CLOB)/.test(u)) return "📝";
    if(/(TIMESTAMP|DATE|TIME)/.test(u)) return "📅";
    if(u.indexOf("BOOL")>=0) return "✓";
    if(/(BLOB|BINARY|BYTEA)/.test(u)) return "📦";
    if(u.indexOf("JSON")>=0) return "🧬";
    if(u.indexOf("UUID")>=0) return "🆔";
    return u.substring(0,1) || "?";
  }}

  // ── 컨텍스트 인식 hint (005 JS 와 동일 정책) ──
  var ANCHORS = {{
    "SELECT":1,"FROM":1,"WHERE":1,"JOIN":1,"ON":1,"AND":1,"OR":1,
    "GROUP":1,"ORDER":1,"HAVING":1,"LIMIT":1,"BY":1,
    "INSERT":1,"UPDATE":1,"DELETE":1,"SET":1,"INTO":1,"VALUES":1,
    "INNER":1,"LEFT":1,"RIGHT":1,"FULL":1,
    "UNION":1,"EXCEPT":1,"INTERSECT":1,
    "AS":1,"WITH":1
  }};
  var CTX_MAP = {{
    "SELECT":"columns_or_star",
    "FROM":"tables","JOIN":"tables","INTO":"tables","UPDATE":"tables",
    "INNER":"join_continue","LEFT":"join_continue",
    "RIGHT":"join_continue","FULL":"join_continue",
    "ON":"columns","WHERE":"columns","AND":"columns","OR":"columns",
    "GROUP_BY":"columns","ORDER_BY":"columns","HAVING":"columns","SET":"columns",
    "LIMIT":"number","DELETE":"from_keyword",
    "VALUES":"any","AS":"any","WITH":"any"
  }};

  function detectContext(textBefore){{
    var s = textBefore
      .replace(/--[^\n]*/g," ")
      .replace(/\/\*[\s\S]*?\*\//g," ")
      .replace(/'[^']*'/g," ")
      .replace(/"[^"]*"/g," ");
    var tokens = s.split(/\s+/).filter(function(t){{ return t.length > 0; }});
    if(tokens.length === 0) return "start";
    // weak anchor (AS/WITH/VALUES) 는 콤마를 지나친 뒤에는 건너뜀 — Python
    // detect_context 와 동일 정책. 'SELECT col AS al, |' 같이 콤마로
    // 새 항목 시작 위치에서 컬럼 추천이 뜨도록.
    var WEAK = {{ "AS":1, "WITH":1, "VALUES":1 }};
    var seenComma = false;
    var last = null, lastIdx = -1;
    for(var i = tokens.length-1; i >= 0; i--){{
      var tok = tokens[i];
      if(tok.indexOf(",") >= 0) seenComma = true;
      var tu = tok.toUpperCase();
      if(ANCHORS[tu]){{
        if(WEAK[tu] && seenComma) continue;
        last = tu; lastIdx = i; break;
      }}
    }}
    if(last === null) return "start";
    if((last === "GROUP" || last === "ORDER") &&
       lastIdx + 1 < tokens.length &&
       tokens[lastIdx+1].toUpperCase() === "BY"){{
      last = last + "_BY";
    }}
    if(last === "BY" && lastIdx > 0){{
      var prev = tokens[lastIdx-1].toUpperCase();
      if(prev === "GROUP" || prev === "ORDER") last = prev + "_BY";
    }}
    return CTX_MAP[last] || "general";
  }}

  function contextHint(cm){{
    var cur = cm.getCursor();
    var line = cm.getLine(cur.line);
    // 양방향 word 경계 — 중간-텍스트 타이핑 시 cursor 뒤 word 문자도 함께
    // replacement 범위에 포함시켜야 popup 이 열리고 인서트 시 단어가 깨지지
    // 않음 ('WHERE' 사이에 X 친 → 'WHXERE' 전체를 'WHERE' 로 대치).
    var start = cur.ch, end = cur.ch;
    while(start > 0 && /[\w_.]/.test(line[start-1])) start--;
    while(end < line.length && /[\w_.]/.test(line[end])) end++;
    var word = line.substring(start, end);
    // 컨텍스트 분석은 cursor 까지의 텍스트만 — 사용자가 작성한 의도가
    // cursor 위치까지 반영되어야 정확.
    var beforeAll = cm.getRange({{line:0,ch:0}}, cur);
    var ctx = detectContext(beforeAll);

    // table_or_alias. qualifier 우선 처리
    // alias 추출은 cursor 까지가 아닌 **전체 문서** 를 스캔 — SELECT 절에서
    // FROM 보다 앞 위치에 있을 때도 뒤쪽 'FROM x AS o' 가 인식되어야 함.
    var dot = word.indexOf(".");
    if(dot > 0){{
      var qual = word.substring(0, dot);
      var fp = word.substring(dot+1).toLowerCase();
      var aliases = extractAliases(cm.getValue());
      var real = aliases[qual];
      if(real && SCHEMA[real]){{
        var list = SCHEMA[real]
          .filter(function(c){{ return c.name.toLowerCase().indexOf(fp) === 0; }})
          .map(function(c){{
            // 표시 라벨에 짧은 타입 이모지 ("id 🔢")
            var disp = c.type ? (c.name + " " + shortType(c.type)) : c.name;
            return {{ text: qual + "." + c.name, displayText: disp }};
          }});
        return {{ list: list, from: CodeMirror.Pos(cur.line, start),
                 to: CodeMirror.Pos(cur.line, end) }};
      }}
    }}

    var cands = [];
    var seenCol = {{}};

    if(ctx === "tables" || ctx === "general" || ctx === "start"){{
      Object.keys(SCHEMA).forEach(function(tname){{
        cands.push({{ text: tname,
                     displayText: tname + "  · table" }});
      }});
    }}
    if(ctx === "columns" || ctx === "columns_or_star" ||
       ctx === "general" || ctx === "start"){{
      Object.keys(SCHEMA).forEach(function(tname){{
        SCHEMA[tname].forEach(function(c){{
          if(seenCol[c.name]) return;
          seenCol[c.name] = 1;
          // 컬럼 추천 표시: "id 🔢  · users" 형태 (타입은 짧은 이모지)
          var disp = c.type
            ? (c.name + " " + shortType(c.type) + "  · " + tname)
            : (c.name + "  · " + tname);
          cands.push({{ text: c.name, displayText: disp }});
        }});
      }});
    }}
    if(ctx === "columns_or_star"){{
      cands.unshift({{ text: "*", displayText: "*  · all columns" }});
    }}
    if(ctx === "join_continue"){{
      cands.push({{ text: "JOIN", displayText: "JOIN  · join" }});
      cands.push({{ text: "OUTER JOIN", displayText: "OUTER JOIN  · join" }});
    }}
    if(ctx === "from_keyword"){{
      cands.push({{ text: "FROM", displayText: "FROM  · keyword" }});
    }}
    // 항상 KEYWORDS / FUNCTIONS 를 fallback 으로 추가 — substring 매칭으로
    // 컨텍스트 외에도 'WHE', 'GR', 'JOI' 같은 부분 입력에 키워드/함수가
    // 자동완성 popup 에 떠야 함. 컨텍스트 specific 후보가 위에 와서 우선.
    var seenText = {{}};
    cands.forEach(function(c){{ seenText[c.text] = 1; }});
    KEYWORDS.forEach(function(k){{
      if(!seenText[k]){{
        cands.push({{ text: k, displayText: k + "  · keyword" }});
        seenText[k] = 1;
      }}
    }});
    FUNCTIONS.forEach(function(f){{
      var t = f + "(";
      if(!seenText[t]){{
        cands.push({{ text: t, displayText: f + "(  · function" }});
        seenText[t] = 1;
      }}
    }});

    var fl = word.toLowerCase();
    if(fl){{
      cands = cands.filter(function(c){{
        return c.text.toLowerCase().indexOf(fl) >= 0;
      }});
    }}
    return {{
      list: cands.slice(0, 50),
      from: CodeMirror.Pos(cur.line, start),
      to:   CodeMirror.Pos(cur.line, end),
    }};
  }}
}})();
"""


# ===== SQLRunnerCM 클래스 =====

class SQLRunnerCM:
    """ipywidgets + 인라인 CodeMirror 5 SQL 편집기 + 실행 위젯.

    Args:
        on_execute: ``f(sql: str) -> Any`` 콜백. ▶ 실행 버튼이나
            Cmd/Ctrl+Enter 단축키로 호출되며, 반환값이 None 이 아니면
            Output 위젯에 ``display(...)`` 로 표시된다.
    """

    def __init__(self,
                 on_execute: Optional[Callable[[str], Any]] = None,
                 on_validate: Optional[Callable[[str], tuple]] = None,
                 history_dir: Optional[str] = ".sql_runner_history") -> None:
        """
        Args:
            on_execute: ``f(sql) -> Any`` SQL 실행 콜백.
            on_validate: ``f(sql) -> (ok, message)`` 사용자 정의 SQL 검증
                콜백 (선택). 미지정 시 내장 ``validate_sql`` 사용.
            history_dir: 실행 이력을 저장할 디렉토리 경로 (CWD 상대 또는
                절대). 파일은 ``YYYY-MM-DD.jsonl`` 1일 1파일로 append-only.
                ``None`` 으로 끄면 in-memory 만 사용. 기본값
                ``".sql_runner_history"`` 는 노트북 디렉토리에 dotted 폴더
                생성 → 다음 노트북 세션에서 자동 로드되어 history 가 이어짐.
        """
        self.tables: dict[str, list[dict]] = {}
        self.notes: dict[str, str] = {}
        self.initial_query: str = ""
        self.on_execute = on_execute
        self.on_validate = on_validate or validate_sql
        self.history_dir = history_dir

        # ── 후속 분석을 위한 실행 상태 ──
        # ▶ 실행 후 다음 셀에서 runner.last_result.head() 같이 접근 가능.
        self.last_query: Optional[str] = None      # 마지막으로 실행한 SQL
        self.last_result: Any = None               # 마지막 실행의 반환값
        self.last_error: Optional[BaseException] = None  # 실패했다면 예외
        # history 는 list 인 동시에 callable — runner.history() 로 보기 좋게
        # 표시 + SQL/전체 복사 버튼 제공. list 메서드는 그대로 동작.
        # 생성 시점에 history_dir 의 .jsonl 파일들을 모두 읽어 누적 로드.
        self.history: _HistoryView = _HistoryView()
        self._load_history_dir()

        self._textarea = None
        self._cursor_text = None    # CM cursor 위치까지의 텍스트 (cursorActivity 동기화용)
        self._run_box = None
        self._output = None
        self._suggest_box = None
        self._validate_box = None   # ❌ + 메시지 표시 위젯 (성공 시 비어있음)
        self._run_btn = None        # ▶ 실행 버튼 (검증 결과에 따라 disabled 토글)
        self._uid = "u" + uuid.uuid4().hex[:10]

    # ----- history 파일 영속화 -----

    def _load_history_dir(self) -> None:
        """``history_dir`` 의 모든 ``*.jsonl`` 파일을 timestamp 순으로 로드.

        result/error 객체 자체는 보존되지 않으며 (DataFrame 등은 직렬화
        부담/보안 우려), entry 의 query · timestamp · status · error_msg ·
        row_count · col_count 만 복원된다. 복원된 entry 는 ``from_file=True``
        플래그가 붙어 UI 에서 "이전 세션" 으로 표시된다.
        """
        import os
        if not self.history_dir or not os.path.isdir(self.history_dir):
            return
        try:
            files = sorted(f for f in os.listdir(self.history_dir)
                           if f.endswith(".jsonl"))
        except OSError:
            return
        loaded: list = []
        for fname in files:
            path = os.path.join(self.history_dir, fname)
            try:
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        status = entry.get("status", "success")
                        msg = entry.get("error_msg")
                        if status == "validation_failed":
                            entry["error"] = SyntaxError(msg or "validation failed")
                        elif status == "error":
                            entry["error"] = RuntimeError(msg or "execution error")
                        else:
                            entry["error"] = None
                        entry["result"] = None
                        entry["from_file"] = True
                        loaded.append(entry)
            except OSError:
                continue
        # timestamp 기준 정렬 (오래된 것부터)
        loaded.sort(key=lambda e: e.get("timestamp", ""))
        self.history.extend(loaded)

    def _append_to_history_dir(self, entry: dict) -> None:
        """현재 세션에서 새로 실행한 entry 를 오늘 날짜의 .jsonl 에 append.

        엔트리는 통과/실패 모두 기록 — 실패 사례도 추후 학습/재시도용으로
        가치가 있음. result 본체는 저장하지 않고 row_count / col_count 만
        보존 (DataFrame 직렬화 부담 + 폐쇄망 데이터 보안).
        """
        import os
        if not self.history_dir:
            return
        ts = entry.get("timestamp") or ""
        date = ts.split(" ")[0] if " " in ts else (
            datetime.date.today().isoformat())
        try:
            os.makedirs(self.history_dir, exist_ok=True)
        except OSError:
            return
        err = entry.get("error")
        if isinstance(err, SyntaxError):
            status = "validation_failed"
        elif err is not None:
            status = "error"
        else:
            status = "success"
        persist = {
            "timestamp": ts,
            "query": entry.get("query") or "",
            "status": status,
            "error_msg": str(err) if err else None,
            "row_count": None,
            "col_count": None,
        }
        result = entry.get("result")
        if result is not None:
            try:
                import pandas as pd
                if isinstance(result, pd.DataFrame):
                    persist["row_count"] = len(result)
                    persist["col_count"] = len(result.columns)
                elif isinstance(result, list):
                    persist["row_count"] = len(result)
            except ImportError:
                pass
        path = os.path.join(self.history_dir, f"{date}.jsonl")
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(persist, ensure_ascii=False) + "\n")
        except OSError:
            pass

    @property
    def query(self) -> str:
        """현재 에디터에 작성된 SQL (▶ 실행 안 했어도 읽기 가능)."""
        return self._textarea.value if self._textarea is not None else self.initial_query

    @property
    def result(self) -> Any:
        """last_result 의 짧은 alias — runner.result 로 바로 접근."""
        return self.last_result

    # ----- 편의 생성자 (007 의 with_sqlite 와 동일 패턴) -----

    @classmethod
    def with_sqlite(cls, db_path: str) -> "SQLRunnerCM":
        """SQLite DB 경로 하나로 thread-safe SQLRunnerCM 즉시 구성.

        ipywidgets 버튼 콜백은 Jupyter 커널의 IO 스레드에서 실행되어 외부
        셀에서 만든 sqlite3.Connection 과 thread 가 다를 수 있다 (그 경우
        ProgrammingError). 이 헬퍼는 매 호출마다 새 connect 를 열고 닫아
        thread 문제를 회피한다. (pandas 필요)
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
                  description: str = "") -> "SQLRunnerCM":
        self.tables[name] = [_normalize_column(c) for c in columns]
        if description:
            self.notes[name] = description
        return self

    def from_dict(self,
                  schema: Mapping[str, Iterable[ColumnSpec]]) -> "SQLRunnerCM":
        for tname, cols in schema.items():
            self.add_table(tname, cols)
        return self

    def from_sqlite(self, path: str) -> "SQLRunnerCM":
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

    def from_dataframes(self,
                        dataframes: Mapping[str, Any]) -> "SQLRunnerCM":
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

    def set_query(self, query: str) -> "SQLRunnerCM":
        self.initial_query = query
        return self

    # ----- 렌더 -----

    def show(self) -> None:
        try:
            import ipywidgets as W
            from IPython.display import display, HTML
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "show() 는 Jupyter + ipywidgets 가 필요합니다."
            ) from e

        # ── 좌측 entity 패널 ──
        # 위젯 객체(테이블/컬럼당 W.Button) 대신 단일 W.HTML 한 덩어리로
        # 렌더. 수백 개 entity 가 있어도 위젯 comm/DOM 비용이 0 이고, 검색창
        # + 자체 JS 로 즉석 필터링한다. 클릭은 컨테이너 단일 delegated
        # listener 가 받아 window['__cmInsert_<uid>'] 를 직접 호출 — Python
        # round-trip 이 없으므로 인서트도 더 빠름.
        tree = W.HTML(
            self._entity_panel_html(),
            layout=W.Layout(width="240px", overflow="auto",
                            max_height="700px",   # 에디터 600px + 추천/액션 여유분
                            border="1px solid #c8ccd0",
                            border_radius="4px"),
        )

        # ── 숨겨진 ipywidgets.Textarea — CM <-> Python 데이터 sync 채널 ──
        # _textarea       : 전체 SQL (▶ 실행 시 읽음)
        # _cursor_text    : 시작부터 CM 커서 위치까지의 텍스트.
        #                   cursorActivity 이벤트마다 JS 가 갱신해 보내므로
        #                   화살표 키로 커서를 옮겨도 _update_suggest 가
        #                   다시 호출되어 컨텍스트 추천이 갱신됨.
        self._textarea = W.Textarea(value=self.initial_query)
        self._textarea.add_class(f"cm-ta-{self._uid}")
        self._cursor_text = W.Textarea(value=self.initial_query)
        self._cursor_text.add_class(f"cm-cursor-{self._uid}")

        # ── CM mount div (ipywidgets.HTML 안에 빈 div) ──
        editor_html = W.HTML(
            f'<div id="cm-mount-{self._uid}" '
            f'class="cm-mount" style="border:1px solid #c8ccd0;'
            f'border-radius:4px;overflow:hidden;min-height:600px"></div>'
        )

        # ── 액션 버튼 + Output ──
        # SQL 복사 (clipboard) 는 폐쇄망에서 차단되는 경우가 많아 제거.
        # 대신 마지막 실행 결과를 CSV / Excel 로 즉시 다운로드.
        run_btn = W.Button(description="▶ 실행 (Cmd/Ctrl+Enter)",
                           button_style="primary",
                           layout=W.Layout(width="auto"))
        self._run_btn = run_btn
        csv_btn = W.Button(description="⬇ CSV 다운로드",
                           tooltip="마지막 실행 결과 (last_result) 를 CSV 로 저장",
                           layout=W.Layout(width="auto"))
        xlsx_btn = W.Button(description="⬇ Excel 다운로드",
                            tooltip="마지막 실행 결과 (last_result) 를 .xlsx 로 저장",
                            layout=W.Layout(width="auto"))
        # 💾 저장 버튼 — 다운로드와 달리 노트북 cwd 에 직접 파일을 떨어뜨려
        # 후속 셀에서 pd.read_csv 로 다시 읽거나 사내 파일 공유에 쓰기 좋음.
        save_csv_btn = W.Button(description="💾 CSV 파일 저장",
                                tooltip="cwd 에 sql_result_<ts>.csv 저장",
                                layout=W.Layout(width="auto"))
        save_xlsx_btn = W.Button(description="💾 Excel 파일 저장",
                                 tooltip="cwd 에 sql_result_<ts>.xlsx 저장",
                                 layout=W.Layout(width="auto"))
        clear_btn = W.Button(description="🗑 지우기",
                             layout=W.Layout(width="auto"))
        run_btn.on_click(self._on_run)
        csv_btn.on_click(self._on_download_csv)
        xlsx_btn.on_click(self._on_download_xlsx)
        save_csv_btn.on_click(self._on_save_csv)
        save_xlsx_btn.on_click(self._on_save_xlsx)
        clear_btn.on_click(self._on_clear)

        self._run_box = W.HBox([run_btn], layout=W.Layout(padding="0"))
        self._run_box.add_class(f"cm-run-{self._uid}")
        actions = W.HBox(
            [self._run_box, csv_btn, xlsx_btn,
             save_csv_btn, save_xlsx_btn, clear_btn],
            layout=W.Layout(padding="4px 0", flex_flow="row wrap"),
        )

        # ── 추천 칩 패널 (005 와 동일 컨셉) ──
        # CM popup 자동완성과 별개로 항상 보이는 컨텍스트 추천. cursor 위치를
        # 알 수 없어 텍스트 끝 기준으로 동작 — 정밀도가 popup 보다 낮은
        # 대신 사용자가 "지금 뭘 칠 수 있는지" 한눈에 보이는 장점이 있음.
        self._suggest_box = W.HBox(
            layout=W.Layout(flex_flow="row wrap", padding="2px 0",
                            min_height="32px"),
        )

        # ── SQL 문법 검증 상태 표시 ──
        # 텍스트 변경마다 on_validate(sql) 호출 → ✓/❌ 와 메시지 표시.
        # 검증 실패 시 run_btn.disabled = True 로 ▶ 실행 차단.
        self._validate_box = W.HTML(
            value="", layout=W.Layout(padding="2px 6px"))

        # ── 결과 Output — 에디터(~30줄)에 공간을 양보, Output 은 컴팩트
        # 사용자가 큰 결과를 보고 싶으면 다음 셀에서 runner.last_result 로
        # 후속 분석을 이어가는 패턴 권장. Output min_height 는 작게.
        self._output = W.Output(
            layout=W.Layout(border="1px solid #d8dde1",
                            min_height="300px",
                            overflow="auto", padding="6px",
                            width="100%"),
        )

        # ── 커서까지의 텍스트 변경 → 추천 칩 갱신 ──
        # _textarea(전체 SQL) 가 아닌 _cursor_text(시작~커서) 를 observe
        # 하므로 화살표로 커서만 이동해도 추천 갱신.
        self._cursor_text.observe(self._on_text_change, names="value")
        self._update_suggest(self.initial_query)

        # ── 전체 SQL 변경 → 문법 검증 ──
        # cursor_text 가 아닌 _textarea (전체 SQL) 변경 시점에만 검증해
        # 화살표 이동 시 불필요한 재검증을 피함.
        self._textarea.observe(self._on_full_text_change, names="value")
        self._update_validation(self.initial_query)

        # ── 우측 상단 패널: 에디터 + 추천 + 액션 ──
        # 결과 Output 은 따로 빼서 셀 전체 너비를 차지하게 만든다.
        right_top = W.VBox([
            W.HTML(
                '<div style="padding:5px 10px;background:#eef0f3;'
                'border:1px solid #d8dde1;border-radius:4px 4px 0 0;'
                'font-size:11px">'
                '<b>SQL Runner (CodeMirror)</b> · 좌측 클릭 → 커서 위치에 인서트 · '
                'Ctrl+Space 자동완성 · Cmd/Ctrl+Enter 실행</div>'
            ),
            editor_html,
            self._textarea,                # display:none 처리됨 (JS)
            self._cursor_text,             # display:none 처리됨 (JS)
            W.HTML(
                '<div style="padding:3px 10px;background:#f7f8fa;'
                'border:1px solid #d8dde1;border-top:0;border-bottom:0;'
                'font-size:11px;color:#6c757d">'
                '💡 추천 (현재 컨텍스트 기반 · 클릭하면 커서 위치에 삽입)'
                '</div>'
            ),
            self._suggest_box,
            self._validate_box,
            actions,
        ], layout=W.Layout(flex="1", min_width="0"))

        # 상단 행: 좌측 트리 + 우측 (에디터/추천/액션)
        top_row = W.HBox([tree, right_top], layout=W.Layout(width="100%"))

        # 하단 결과 영역: 셀 전체 너비
        result_section = W.VBox([
            W.HTML(
                '<div style="padding:3px 10px;margin-top:6px;'
                'background:#eef0f3;border:1px solid #d8dde1;'
                'border-radius:4px 4px 0 0;font-size:11px;'
                'color:#1f2329"><b>📤 실행 결과</b>  '
                '<span style="color:#6c757d">'
                '· runner.last_result · runner.history() 로 이력 보기 / 복사'
                '</span></div>'
            ),
            self._output,
        ], layout=W.Layout(width="100%"))

        # 최상위: 상단 행 + 결과 (전체 너비)
        layout = W.VBox([top_row, result_section],
                        layout=W.Layout(width="100%"))

        # 1. CSS + JS 번들 1회 주입
        display(HTML(self._cm_bundle_html()))
        # 2. ipywidgets 레이아웃
        display(layout)
        # 3. 부트스트랩 JS — 위 레이아웃의 hidden textarea / mount 를 찾아
        #    CodeMirror 인스턴스 mount
        display(HTML(self._cm_bootstrap_html()))
        # 4. Entity 패널 부트스트랩 — 검색 필터 + 클릭 delegation 결합
        #    (W.HTML 안의 <script> 는 일부 Jupyter frontend 에서 실행되지
        #    않을 수 있으므로 부트스트랩과 동일한 패턴으로 별도 발행)
        display(HTML(self._entity_panel_bootstrap_html()))

    # ----- 내부 헬퍼 -----

    def _cm_bundle_html(self) -> str:
        """CodeMirror CSS+JS 한 번에 inject. 노트북당 1회만 호출되어도
        충분 (각 인스턴스가 매번 호출해도 idempotent — 브라우저는 동일 함수
        선언을 무시 / 재선언하지만 동작 영향 없음)."""
        return (
            "<style>"
            + _CM_CSS + "\n" + _CM_HINT_CSS + "\n" + _CM_THEME_CSS
            + "\n.cm-mount .CodeMirror{height:auto;min-height:600px;"
            "font-family:'SF Mono',Menlo,Consolas,monospace;font-size:13px}"
            + "</style>"
            + "<script>"
            + _CM_JS + "\n" + _CM_SQL_JS + "\n" + _CM_HINT_JS
            + "</script>"
        )

    def _cm_bootstrap_html(self) -> str:
        # 스키마 → JS 객체 (table → [{name,type,doc}])
        schema_for_js = {
            tname: [{"name": c["name"], "type": c.get("type", ""),
                     "doc": c.get("doc", "")} for c in cols]
            for tname, cols in self.tables.items()
        }
        js = _BOOTSTRAP_JS_TPL.format(
            uid=self._uid,
            schema_json=json.dumps(schema_for_js, ensure_ascii=False),
            keywords_json=json.dumps(_KEYWORDS),
            functions_json=json.dumps(_FUNCTIONS),
        )
        return f"<script>{js}</script>"

    def _entity_panel_html(self) -> str:
        """좌측 entity 패널의 HTML 마크업 (CSS + 검색 input + 테이블/컬럼).

        위젯 객체(W.Button, W.HBox 다수) 를 만들지 않고 단일 W.HTML 로
        렌더하므로 수백 entity 도 즉시 표시됨. 클릭/검색은
        _entity_panel_bootstrap_html() 의 delegated listener 가 처리.

        XSS 안전:
          · 표시 텍스트와 title 은 html.escape() 통과
          · data-snippet 은 urllib.parse.quote() 인코딩 → JS 에서
            decodeURIComponent 로 복원 (인용부호/태그 모두 안전)
        """
        from urllib.parse import quote

        uid = self._uid
        parts: list[str] = []
        # 패널 스코프 CSS — 다른 인스턴스/페이지 스타일과 충돌 회피.
        # 색/크기는 기존 W.Button 시절 (테이블 #fafbfc, 컬럼 #ffffff,
        # 헤더 #eef0f3, 폭 240px) 을 그대로 재현.
        parts.append(
            f"<style>"
            f"#entity-panel-{uid}{{font-size:11px;box-sizing:border-box}}"
            f"#entity-panel-{uid} *,"
            f"#entity-panel-{uid} *::before,"
            f"#entity-panel-{uid} *::after{{box-sizing:border-box}}"
            f"#entity-panel-{uid} .ep-header{{"
            f"padding:8px 10px;font-weight:600;font-size:12px;"
            f"background:#eef0f3;border-bottom:1px solid #d8dde1;"
            f"position:sticky;top:0;z-index:1}}"
            f"#entity-panel-{uid} .ep-search-wrap{{"
            f"padding:6px 8px;background:#f7f8fa;"
            f"border-bottom:1px solid #e3e6e9;"
            f"position:sticky;top:30px;z-index:1}}"
            f"#entity-panel-{uid} .ep-search{{"
            f"width:100%;padding:4px 8px;font-size:11px;"
            f"border:1px solid #c8ccd0;border-radius:3px;outline:none}}"
            f"#entity-panel-{uid} .ep-search:focus{{border-color:#2563eb}}"
            f"#entity-panel-{uid} .ep-empty{{"
            f"padding:12px;color:#888;font-size:11px}}"
            f"#entity-panel-{uid} .ep-tbl{{margin-bottom:2px}}"
            f"#entity-panel-{uid} .ep-tbl-btn{{"
            f"display:block;width:218px;height:26px;"
            f"margin:2px 8px 1px 8px;padding:0 6px;"
            f"background:#fafbfc;border:1px solid #c8ccd0;"
            f"border-radius:3px;cursor:pointer;"
            f"font-size:11px;text-align:left;color:#1f2329;"
            f"white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}"
            f"#entity-panel-{uid} .ep-tbl-btn:hover{{background:#eef0f3}}"
            f"#entity-panel-{uid} .ep-note{{"
            f"padding:0 8px 2px 18px;font-size:10px;"
            f"color:#6c757d;font-style:italic}}"
            f"#entity-panel-{uid} .ep-cols{{"
            f"display:flex;flex-flow:row wrap;"
            f"padding:0 8px 6px 14px;gap:2px}}"
            f"#entity-panel-{uid} .ep-col-btn{{"
            f"height:22px;padding:0 6px;"
            f"background:#fff;border:1px solid #d0d4d8;"
            f"border-radius:3px;cursor:pointer;"
            f"font-size:11px;color:#1f2329;margin:1px}}"
            f"#entity-panel-{uid} .ep-col-btn:hover{{"
            f"background:#f0f4ff;border-color:#2563eb}}"
            f"#entity-panel-{uid} .ep-no-match{{"
            f"display:none;padding:8px 12px;"
            f"color:#888;font-size:11px;font-style:italic}}"
            f"</style>"
        )
        parts.append(f'<div id="entity-panel-{uid}" class="entity-panel">')
        parts.append('<div class="ep-header">📚 Entities</div>')
        parts.append(
            '<div class="ep-search-wrap">'
            '<input type="search" class="ep-search" '
            'placeholder="🔎 검색 (테이블/컬럼)..." '
            'autocomplete="off" spellcheck="false"></div>'
        )
        if not self.tables:
            parts.append(
                '<div class="ep-empty">'
                '등록된 테이블이 없습니다.<br>'
                '<code>add_table(...)</code> / <code>from_dict(...)</code> '
                '로 추가하세요.</div>'
            )
        else:
            for tname, cols in self.tables.items():
                tname_q = quote(tname, safe="")
                note = self.notes.get(tname, "") or ""
                parts.append(
                    f'<div class="ep-tbl" '
                    f'data-tname="{escape(tname.lower())}">'
                )
                parts.append(
                    f'<button type="button" class="ep-btn ep-tbl-btn" '
                    f'data-snippet="{tname_q}" '
                    f'title="{escape(note)}">'
                    f'📋 {escape(tname)}  ({len(cols)})</button>'
                )
                if note:
                    parts.append(
                        f'<div class="ep-note">{escape(note)}</div>'
                    )
                parts.append('<div class="ep-cols">')
                for c in cols:
                    cname = c["name"]
                    cname_q = quote(cname, safe="")
                    doc = c.get("doc", "") or ""
                    parts.append(
                        f'<button type="button" class="ep-btn ep-col-btn" '
                        f'data-snippet="{cname_q}" '
                        f'data-cname="{escape(cname.lower())}" '
                        f'title="{escape(doc)}">{escape(cname)}</button>'
                    )
                parts.append('</div>')   # ep-cols
                parts.append('</div>')   # ep-tbl
            # 검색 결과 0개일 때 안내 메시지 (JS 에서 toggle)
            parts.append(
                '<div class="ep-no-match">검색 결과가 없습니다.</div>'
            )
        parts.append('</div>')
        return ''.join(parts)

    def _entity_panel_bootstrap_html(self) -> str:
        """Entity 패널의 클릭 delegation + 검색 필터 JS.

        ipywidgets 가 layout 을 비동기로 mount 할 수 있어 setInterval 로
        폴링한 뒤 1회만 wire-up. CM mount 의 부트스트랩과 동일 패턴.
        """
        uid = self._uid
        # JSON encode UID into a JS string literal (안전).
        uid_lit = json.dumps(uid)
        js = (
            "(function(){"
            f"var UID={uid_lit};"
            "function tryWire(){"
            "var panel=document.getElementById('entity-panel-'+UID);"
            "if(!panel)return false;"
            "if(panel.dataset.wired==='1')return true;"
            "panel.dataset.wired='1';"
            "panel.addEventListener('click',function(e){"
            "var btn=e.target.closest&&e.target.closest('.ep-btn');"
            "if(!btn||!panel.contains(btn))return;"
            "var raw=btn.dataset.snippet||'';"
            "var snippet;"
            "try{snippet=decodeURIComponent(raw);}catch(_e){snippet=raw;}"
            "var fn=window['__cmInsert_'+UID];"
            "if(fn)fn(snippet);"
            "});"
            "var search=panel.querySelector('.ep-search');"
            "var noMatch=panel.querySelector('.ep-no-match');"
            "if(search){"
            "search.addEventListener('input',function(){"
            "var q=(search.value||'').toLowerCase().trim();"
            "var tbls=panel.querySelectorAll('.ep-tbl');"
            "var anyVisible=false;"
            "for(var i=0;i<tbls.length;i++){"
            "var tbl=tbls[i];"
            "var cbs=tbl.querySelectorAll('.ep-col-btn');"
            "if(!q){"
            "tbl.style.display='';"
            "for(var j=0;j<cbs.length;j++)cbs[j].style.display='';"
            "anyVisible=true;"
            "continue;"
            "}"
            "var tname=tbl.dataset.tname||'';"
            "if(tname.indexOf(q)>=0){"
            "tbl.style.display='';"
            "for(var j=0;j<cbs.length;j++)cbs[j].style.display='';"
            "anyVisible=true;"
            "}else{"
            "var any=false;"
            "for(var j=0;j<cbs.length;j++){"
            "var cn=cbs[j].dataset.cname||'';"
            "if(cn.indexOf(q)>=0){cbs[j].style.display='';any=true;}"
            "else{cbs[j].style.display='none';}"
            "}"
            "tbl.style.display=any?'':'none';"
            "if(any)anyVisible=true;"
            "}"
            "}"
            "if(noMatch)noMatch.style.display=(q&&!anyVisible)?'block':'none';"
            "});"
            "}"
            "return true;"
            "}"
            "if(!tryWire()){"
            "var tries=0;"
            "var iv=setInterval(function(){"
            "tries++;"
            "if(tryWire()||tries>80){clearInterval(iv);}"
            "},50);"
            "}"
            "})();"
        )
        return f"<script>{js}</script>"

    def _make_inserter(self, snippet: str) -> Callable[[Any], None]:
        """좌측 entity 버튼 클릭 시 CM 커서 위치에 인서트.

        JS 사이드의 `__cmInsert_<uid>` 가 mount 시 window 에 등록됨.
        ipywidgets 의 button click 은 Python 콜백 → 다시 JS 호출이 필요해
        IPython.display(HTML) 로 짧은 1-shot 스크립트를 발행한다.
        """
        snippet_js = (snippet
                      .replace("\\", "\\\\")
                      .replace("`", "\\`")
                      .replace("$", "\\$"))

        def _handler(_btn: Any) -> None:
            from IPython.display import display, HTML
            with self._output:
                # 인서트는 결과 영역을 어지럽히지 않도록 invisible 영역에 발행
                display(HTML(
                    "<script>"
                    f"(function(){{"
                    f"var fn = window['__cmInsert_{self._uid}'];"
                    f"if(fn) fn(`{snippet_js}`);"
                    f"}})();"
                    "</script>"
                ))
                # 위 스크립트만 1회 발행하면 되고 결과 영역은 다시 비움
                self._output.clear_output()
        return _handler

    def _on_text_change(self, change: Mapping[str, Any]) -> None:
        new_text = change.get("new", "")
        self._update_suggest(new_text)

    def _on_full_text_change(self, change: Mapping[str, Any]) -> None:
        """전체 SQL 변경마다 문법 검증 + ▶ 실행 버튼 활성/비활성 토글."""
        self._update_validation(change.get("new", ""))

    def _update_validation(self, sql: str) -> None:
        """on_validate(sql) 호출 → 상태 박스/실행 버튼 갱신.

        검증 콜백 자체가 예외를 던지면 안전하게 graceful 처리해 (검증 통과)
        취급 — 사용자 정의 검증이 깨져도 실행은 막지 않는다.
        """
        try:
            ok, msg = self.on_validate(sql) if self.on_validate else (True, None)
        except Exception as e:
            ok, msg = True, None
            print(f"⚠ on_validate 콜백 예외 (실행은 허용): "
                  f"{type(e).__name__}: {e}")
        if self._run_btn is not None:
            self._run_btn.disabled = not ok
            self._run_btn.tooltip = "" if ok else (msg or "SQL 검증 실패")
        if self._validate_box is not None:
            # 통과 시에는 메시지 숨김 (조용한 성공) — 실패 시에만 빨간 표시
            if ok:
                self._validate_box.value = ""
            else:
                self._validate_box.value = (
                    "<span style='color:#b91c1c;font-size:11px'>"
                    f"❌ {escape(msg or 'SQL 검증 실패')}</span>"
                )

    def _update_suggest(self, text: str) -> None:
        """추천 칩 패널 갱신. 컨텍스트 라벨 + 클릭 가능 Button 칩 렌더."""
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

        # 첫 칩 (컨텍스트 라벨) 의 height/align 을 옆에 오는 ipywidgets.Button
        # (height=22px) 과 픽셀 단위로 맞추기 위해 inline-flex + align-items
        # + box-sizing border-box. line-height 도 명시해 텍스트 수직 중앙.
        children: list = [
            W.HTML(
                f'<span style="display:inline-flex;align-items:center;'
                f'height:22px;box-sizing:border-box;'
                f'padding:0 10px;margin:2px 6px 2px 0;'
                f'background:#fff;border:1px solid #c8ccd0;border-radius:11px;'
                f'font-size:11px;line-height:1;color:#1f2329;white-space:nowrap">'
                f'<b style="margin-right:4px">컨텍스트:</b>'
                f'{escape(ctx_label)}</span>'
            ),
        ]

        # alias 추출은 전체 SQL 텍스트 (_textarea) 를 기준으로 — cursor 가
        # SELECT 절에 있어도 뒤쪽 FROM/JOIN 의 AS alias 가 잡혀야 함.
        full_text = self._textarea.value if self._textarea is not None else text
        sugs = get_suggestions(text, self.tables, full_text=full_text)
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
            for s in sugs[:18]:
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

        if self._suggest_box is not None:
            self._suggest_box.children = children

    def execute(self, sql: str) -> Any:
        """SQL 을 실행하고 history 에 누적. ``show()`` 의 ▶ 실행 버튼과
        동일한 동작을 노트북 외부 코드에서 호출 가능하게 노출.

        절차:
          1. ``on_validate(sql)`` 검증 → 실패 시 ``SyntaxError`` raise
          2. ``on_execute(sql)`` 실행 → 결과 반환
          3. 성공/실패 양쪽 모두 timestamp 와 함께 ``self.history`` 에 append,
             ``history_dir`` 가 설정된 경우 그날의 ``YYYY-MM-DD.jsonl`` 에도
             자동 append (파일 저장은 완전히 내부 동작).

        Returns:
            ``on_execute(sql)`` 의 반환값.
        Raises:
            SyntaxError: 검증 실패.
            RuntimeError: ``on_execute`` 미등록.
            Exception: ``on_execute`` 가 던진 예외 그대로 전파.
        """
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.last_query = sql
        if self.on_validate is not None:
            try:
                ok, msg = self.on_validate(sql)
            except Exception:
                ok, msg = True, None
            if not ok:
                err = SyntaxError(msg or "SQL validation failed")
                self.last_error = err
                self.last_result = None
                entry = {"timestamp": ts, "query": sql,
                         "result": None, "error": err}
                self.history.append(entry)
                self._append_to_history_dir(entry)
                raise err
        if self.on_execute is None:
            raise RuntimeError(
                "on_execute 콜백이 등록되지 않았습니다. "
                "SQLRunnerCM(on_execute=fn) 으로 주입 후 사용하세요."
            )
        try:
            result = self.on_execute(sql)
        except Exception as e:
            self.last_error = e
            self.last_result = None
            entry = {"timestamp": ts, "query": sql,
                     "result": None, "error": e}
            self.history.append(entry)
            self._append_to_history_dir(entry)
            raise
        self.last_error = None
        self.last_result = result
        entry = {"timestamp": ts, "query": sql,
                 "result": result, "error": None}
        self.history.append(entry)
        self._append_to_history_dir(entry)
        return result

    def _on_run(self, _btn: Any) -> None:
        """show() 의 ▶ 실행 버튼 핸들러 — execute() 를 호출하고 결과 또는
        에러를 Output 위젯에 렌더. 파일 저장은 execute() 내부에서 자동 수행."""
        sql = self._textarea.value if self._textarea is not None else ""
        with self._output:
            self._output.clear_output()
            if not sql.strip():
                print("⚠ SQL 이 비어있습니다.")
                return
            try:
                result = self.execute(sql)
            except SyntaxError as e:
                print(f"❌ SQL 문법 오류: {e}")
                print("   에디터를 확인하고 수정 후 다시 실행해주세요.")
                return
            except RuntimeError as e:
                print(str(e))
                print(f"\nSQL:\n{sql}")
                return
            except Exception as e:
                import traceback
                print(f"❌ {type(e).__name__}: {e}")
                traceback.print_exc()
                return
            self._render_result(result)

    def _render_result(self, result: Any) -> None:
        """반환값을 Output 위젯에 적절히 렌더.

        DataFrame 인 경우 모든 컬럼/충분한 행을 잘리지 않게 보이도록 pandas
        옵션을 임시 변경하고, HTML 표 + 행/열 카운트 메시지를 함께 출력.
        """
        from IPython.display import display
        if result is None:
            print("✓ 실행 완료 (반환값 없음)")
            return
        try:
            import pandas as pd
            if isinstance(result, pd.DataFrame):
                with pd.option_context(
                    "display.max_columns", None,
                    "display.width", None,
                    "display.max_colwidth", 200,
                    "display.max_rows", 500,
                    "display.expand_frame_repr", False,
                ):
                    display(result)
                print(f"\n[{len(result)} rows × {len(result.columns)} columns]")
                return
            # list[dict] / list[tuple] / dict 등도 보기 좋게 시도
            if isinstance(result, list) and result and isinstance(result[0], dict):
                try:
                    df = pd.DataFrame(result)
                    with pd.option_context(
                        "display.max_columns", None,
                        "display.width", None,
                        "display.max_colwidth", 200,
                        "display.max_rows", 500,
                        "display.expand_frame_repr", False,
                    ):
                        display(df)
                    print(f"\n[{len(df)} rows × {len(df.columns)} columns]  "
                          f"(list[dict] → DataFrame 으로 자동 변환)")
                    return
                except Exception:
                    pass
        except ImportError:
            pass
        display(result)

    def _on_download_csv(self, _btn: Any) -> None:
        """마지막 실행 결과를 CSV 로 즉시 다운로드.

        clipboard 가 차단되는 폐쇄망에서도 동작하도록 base64 data URI →
        anchor.click() 패턴 사용. 외부 네트워크 0.
        """
        self._download_result("csv")

    def _on_download_xlsx(self, _btn: Any) -> None:
        """마지막 실행 결과를 Excel (.xlsx) 로 다운로드.

        openpyxl 또는 xlsxwriter 가 필요 (사내 미러 등록본 기준 통상 가용).
        없으면 안내 메시지로 fallback.
        """
        self._download_result("xlsx")

    def _on_save_csv(self, _btn: Any) -> None:
        """마지막 실행 결과를 노트북 cwd 에 .csv 파일로 저장."""
        self._save_result_to_cwd("csv")

    def _on_save_xlsx(self, _btn: Any) -> None:
        """마지막 실행 결과를 노트북 cwd 에 .xlsx 파일로 저장."""
        self._save_result_to_cwd("xlsx")

    def _save_result_to_cwd(self, fmt: str) -> None:
        """결과를 노트북 작업 디렉토리(cwd) 에 파일로 저장.

        다운로드(브라우저 data URI 자동 클릭) 가 아니라 파일시스템에 직접
        떨어뜨려 후속 셀에서 `pd.read_csv(...)` 로 다시 읽거나 사내 파일
        공유에 쓰는 워크플로우를 지원. 저장 후 IPython.FileLink 로 경로를
        클릭 가능 링크로 안내.
        """
        from IPython.display import display, HTML, FileLink
        import datetime
        import os

        with self._output:
            self._output.clear_output()
            df = self._coerce_to_df()
            if df is None:
                return

            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            fname = f"sql_result_{ts}.{fmt}"
            path = os.path.join(os.getcwd(), fname)
            try:
                if fmt == "csv":
                    # utf-8-sig: Excel 에서 한글 깨짐 없이 열림
                    df.to_csv(path, index=False, encoding="utf-8-sig")
                else:
                    # openpyxl 우선, 없으면 xlsxwriter (다운로드와 동일 로직)
                    try:
                        df.to_excel(path, index=False, engine="openpyxl")
                    except (ImportError, ValueError):
                        try:
                            df.to_excel(path, index=False, engine="xlsxwriter")
                        except (ImportError, ValueError):
                            print("⚠ Excel 엔진 (openpyxl 또는 xlsxwriter) 미설치.")
                            print("CSV 저장은 정상 동작합니다.")
                            return
            except PermissionError as e:
                print(f"❌ 저장 실패 (권한 없음): {e}")
                return
            except OSError as e:
                print(f"❌ 저장 실패 (파일시스템): {e}")
                return

            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            display(HTML(
                f'<div style="padding:6px 10px;font-size:12px;'
                f'background:#ecfdf5;border:1px solid #86efac;'
                f'border-radius:4px;color:#065f46;margin-bottom:4px">'
                f'✓ 저장 완료 · '
                f'<code style="background:#fff;padding:1px 4px;'
                f'border-radius:3px">{escape(path)}</code> '
                f'({size:,} bytes, {len(df)} rows × {len(df.columns)} cols)'
                f'</div>'
            ))
            # FileLink 는 셀 출력에 클릭 가능 링크를 렌더 — 새 탭에서 파일 보기
            # 또는 우클릭 → 다른 이름으로 저장으로 원본 그대로 다운로드 가능.
            display(FileLink(path))

    def _coerce_to_df(self) -> Optional[Any]:
        """last_result 를 DataFrame 으로 정규화 (CSV/Excel 저장·다운로드 공통).

        반환:
          pandas.DataFrame  — 정상 변환됨
          None              — last_result 없음, pandas 미설치, 또는 변환 불가
        실패 사유는 self._output 에 직접 print (호출 측은 None 만 보고 return).
        """
        if self.last_result is None:
            print("⚠ 다운로드/저장할 결과가 없습니다. ▶ 실행 후 시도해 주세요.")
            return None
        try:
            import pandas as pd
        except ImportError:
            print("⚠ pandas 미설치 — CSV/Excel 출력은 pandas 가 필요합니다.")
            return None
        df = self.last_result
        if isinstance(df, list) and df and isinstance(df[0], dict):
            df = pd.DataFrame(df)
        if not isinstance(df, pd.DataFrame):
            print(f"⚠ 결과가 DataFrame/list[dict] 형식이 아니라 출력 불가 "
                  f"(type={type(df).__name__}).")
            return None
        return df

    def _download_result(self, fmt: str) -> None:
        from IPython.display import display, HTML
        import base64, datetime, io

        with self._output:
            self._output.clear_output()
            df = self._coerce_to_df()
            if df is None:
                return

            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            try:
                if fmt == "csv":
                    buf = io.BytesIO()
                    df.to_csv(buf, index=False, encoding="utf-8-sig")
                    payload = buf.getvalue()
                    mime = "text/csv;charset=utf-8"
                    fname = f"sql_result_{ts}.csv"
                else:   # xlsx
                    buf = io.BytesIO()
                    try:
                        df.to_excel(buf, index=False, engine="openpyxl")
                    except (ImportError, ValueError):
                        try:
                            buf = io.BytesIO()
                            df.to_excel(buf, index=False, engine="xlsxwriter")
                        except (ImportError, ValueError) as e2:
                            print("⚠ Excel 엔진 (openpyxl 또는 xlsxwriter) 미설치.")
                            print("CSV 다운로드는 정상 동작합니다.")
                            return
                    payload = buf.getvalue()
                    mime = ("application/vnd.openxmlformats-officedocument."
                            "spreadsheetml.sheet")
                    fname = f"sql_result_{ts}.xlsx"
            except Exception as e:
                print(f"❌ 변환 실패: {type(e).__name__}: {e}")
                return

            b64 = base64.b64encode(payload).decode("ascii")
            href = f"data:{mime};base64,{b64}"
            # anchor 자동 클릭 — 브라우저가 다운로드 다이얼로그 띄움.
            # 클릭 후 사라지지 않도록 명시 링크도 함께 노출 (수동 클릭 fallback).
            display(HTML(
                f'<div style="padding:4px 8px;color:#047857;font-size:12px">'
                f'✓ {fname} 준비됨 ({len(payload):,} bytes, '
                f'{len(df)} rows × {len(df.columns)} cols)</div>'
                f'<a id="dl-{self._uid}-{ts}" href="{href}" '
                f'download="{fname}" '
                f'style="display:inline-block;padding:4px 10px;'
                f'background:#2563eb;color:#fff;text-decoration:none;'
                f'border-radius:4px;font-size:12px;margin:2px 0">'
                f'⬇ {fname} 직접 다운로드</a>'
                f'<script>(function(){{ '
                f'var a=document.getElementById("dl-{self._uid}-{ts}"); '
                f'if(a) a.click(); }})();</script>'
            ))

    def _on_clear(self, _btn: Any) -> None:
        if self._textarea is None:
            return
        from IPython.display import display, HTML
        self._textarea.value = ""
        # CM 본체도 비움 (textarea 만 비우면 CM 은 자기 buffer 를 그대로 보여줌)
        with self._output:
            self._output.clear_output()
            display(HTML(
                "<script>(function(){"
                f"var ed = window['__cmEditor_{self._uid}'];"
                "if(ed){ ed.setValue(''); ed.focus(); }"
                "})();</script>"
            ))
            self._output.clear_output()


# ===== __main__ =====

if __name__ == "__main__":
    # CLI 검증 — Jupyter 없이도 단위 동작 점검
    print("sql_codemirror.py — CodeMirror 인라인 SQL 편집기 (single-file)")
    print(f"  bundle sizes:")
    print(f"    codemirror.min.js: {len(_CM_JS):>7,} bytes")
    print(f"    sql.min.js       : {len(_CM_SQL_JS):>7,} bytes")
    print(f"    show-hint.min.js : {len(_CM_HINT_JS):>7,} bytes")
    print(f"    codemirror.css   : {len(_CM_CSS):>7,} bytes")
    print(f"    show-hint.css    : {len(_CM_HINT_CSS):>7,} bytes")
    print(f"    dracula.css      : {len(_CM_THEME_CSS):>7,} bytes")
    print()

    runner = SQLRunnerCM()
    runner.add_table("users", ["id", "name", "email"], "사용자 마스터")
    runner.add_table("orders", [
        ("id", "INT"), ("user_id", "INT"),
        ("amount", "REAL"), ("status", "TEXT"),
    ])
    print(f"등록 테이블: {list(runner.tables.keys())}")
    print(f"orders 컬럼: {[c['name'] for c in runner.tables['orders']]}")
    print(f"runner._uid: {runner._uid}")
    print()
    print("Jupyter 노트북에서 사용 예시:")
    print("    from sql_codemirror import SQLRunnerCM")
    print("    runner = SQLRunnerCM.with_sqlite('./demo.db')")
    print("    runner.show()")
