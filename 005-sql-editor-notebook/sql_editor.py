"""
SQL Editor for Jupyter Notebook (single-file, self-contained HTML + JS).

원본 출처:
    - 오리지널 구현. 영감만 차용: Snowflake Worksheet · BigQuery 콘솔 · DBeaver
      등 일반적인 SQL 에디터 UX (좌측 entity 트리 + 우측 쿼리 + 컨텍스트 자동완성).
      코드 복제 아님.
라이선스: MIT
생성: Code Conversion Agent

기능 요약
--------
  1) 좌측 entity 트리 — 등록된 테이블·컬럼을 펼침/접기 가능. 컬럼 더블클릭 시
     커서 위치에 이름 삽입.
  2) 우측 SQL 에디터 — `<textarea>` 기반. Tab=2칸 들여쓰기, 한글 입력 지원,
     필요시 직접 selection 조작.
  3) 컨텍스트 인식 자동완성 — 직전 키워드로 다음 토큰 종류를 추정해 추천:
       FROM/JOIN     → 테이블
       SELECT        → 컬럼 (모든 테이블)
       WHERE/AND/OR  → 컬럼 + 비교 연산자
       ON            → 컬럼
       GROUP/ORDER BY → 컬럼
       table_name.   → 해당 테이블 컬럼
       그 외        → 키워드 + 테이블 + 컬럼 전체
  4) 단축키 — Ctrl+Space 강제 호출 · ↑↓ 이동 · Enter/Tab 확정 · Esc 닫기
  5) 항상 표시되는 하단 제안 패널 — 현재 컨텍스트와 최우선 후보 노출
  6) 외부 네트워크/CDN 0, 단일 .py 반입.

사용 예시
--------
    from sql_editor import SQLEditor

    editor = SQLEditor()
    editor.add_table("users", ["id", "name", "email", "created_at"])
    editor.add_table("orders", [
        ("id", "INTEGER"),
        ("user_id", "INTEGER"),
        ("amount", "REAL"),
        ("status", "TEXT"),
    ], description="주문 트랜잭션")
    editor.show()

또는 헬퍼:
    editor.from_dict({"users": [...], "orders": [...]})
    editor.from_sqlite("data.db")
    editor.from_dataframes({"df1": df1, "df2": df2})  # pandas
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any, Iterable, Mapping, Optional, Union

# ===== 타입 alias =====

ColumnSpec = Union[
    str,                        # "id"
    tuple,                      # ("id", "INTEGER") 또는 ("id", "INTEGER", "doc")
    Mapping[str, Any],          # {"name": "id", "type": "INTEGER", "doc": "..."}
]


# ===== 1. SQLEditor 클래스 =====

class SQLEditor:
    """Jupyter 셀에서 동작하는 SQL 편집기.

    `add_table` 등으로 스키마를 등록한 뒤 `show()` 를 호출하면 self-contained
    HTML 위젯이 셀 output 에 렌더된다. 모든 자동완성 로직은 임베드된 JS 가
    수행 — Python 커널과의 통신은 없음.
    """

    def __init__(self) -> None:
        self.tables: dict[str, list[dict]] = {}
        self.notes: dict[str, str] = {}
        self.initial_query: str = ""

    # ----- 스키마 등록 -----

    def add_table(self, name: str,
                  columns: Iterable[ColumnSpec],
                  description: str = "") -> "SQLEditor":
        """테이블 + 컬럼 목록 등록. columns 는 다양한 형식 허용.

        Examples:
            add_table("users", ["id", "name"])
            add_table("orders", [("id", "INTEGER"), ("amt", "REAL", "주문액")])
            add_table("p", [{"name": "id", "type": "INT", "doc": "..."}])
        """
        self.tables[name] = [self._normalize_column(c) for c in columns]
        if description:
            self.notes[name] = description
        return self

    def from_dict(self, schema: Mapping[str, Iterable[ColumnSpec]]) -> "SQLEditor":
        """간이 등록: {"테이블": [컬럼들]}"""
        for tname, cols in schema.items():
            self.add_table(tname, cols)
        return self

    def from_sqlite(self, path: str) -> "SQLEditor":
        """SQLite DB 파일에서 스키마 자동 추출 (stdlib 만 사용)."""
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
                    label = "PK " + cname if pk else cname
                    cols.append({"name": cname, "type": ctype or "", "doc": label if pk else ""})
                self.tables[t] = [self._normalize_column(c) for c in cols]
        finally:
            conn.close()
        return self

    def from_dataframes(self, dataframes: Mapping[str, Any]) -> "SQLEditor":
        """pandas DataFrame 묶음에서 스키마 추출. {name: df}"""
        for name, df in dataframes.items():
            cols: list[ColumnSpec] = []
            try:
                for col, dtype in zip(df.columns, df.dtypes):
                    cols.append({"name": str(col), "type": str(dtype), "doc": ""})
            except AttributeError as e:
                raise TypeError(
                    f"from_dataframes 의 값은 pandas.DataFrame 이어야 합니다 ({name})"
                ) from e
            self.tables[name] = [self._normalize_column(c) for c in cols]
        return self

    def set_query(self, query: str) -> "SQLEditor":
        """초기 쿼리 텍스트 설정."""
        self.initial_query = query
        return self

    @staticmethod
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

    # ----- 렌더 -----

    def show(self) -> None:
        """Jupyter 셀에 에디터 위젯을 렌더."""
        try:
            from IPython.display import HTML, display
        except ImportError as e:
            raise RuntimeError(
                "show() 는 Jupyter / IPython 환경이 필요합니다."
            ) from e
        display(HTML(self.to_html()))

    def to_html(self) -> str:
        """현재 스키마로 self-contained HTML 문자열 생성."""
        editor_id = "sqle-" + uuid.uuid4().hex[:8]
        schema_payload = {
            "tables": self.tables,
            "notes": self.notes,
            "initial_query": self.initial_query,
        }
        # </ 는 브라우저 파서가 </script> 로 오인할 수 있어 이스케이프
        schema_json = (
            json.dumps(schema_payload, ensure_ascii=False, default=str)
            .replace("</", "<\\/")
        )
        return (_EDITOR_HTML_TEMPLATE
                .replace("{{ID}}", editor_id)
                .replace("{{SCHEMA_JSON}}", schema_json))

    def save_html(self, path: str) -> str:
        """HTML 파일로 저장 (브라우저로 직접 열기용)."""
        with open(path, "w", encoding="utf-8") as f:
            f.write(
                "<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<title>SQL Editor</title></head><body>"
                + self.to_html()
                + "</body></html>"
            )
        return path


# ===== 2. HTML 템플릿 (self-contained: CSS + HTML + JS) =====

_EDITOR_HTML_TEMPLATE = r"""
<style>
  .{{ID}}-root {
    display: flex;
    height: 480px;
    width: 100%;
    max-width: 1100px;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI",
                 "Apple SD Gothic Neo", "Malgun Gothic", sans-serif;
    border: 1px solid #c8ccd0;
    border-radius: 6px;
    overflow: hidden;
    background: #fff;
    color: #1f2329;
  }
  .{{ID}}-left {
    width: 232px;
    border-right: 1px solid #d8dde1;
    background: #f7f8fa;
    overflow-y: auto;
    overflow-x: hidden;
    flex-shrink: 0;
  }
  .{{ID}}-left h3 {
    margin: 0;
    padding: 8px 10px;
    font-size: 12px;
    font-weight: 600;
    color: #4a4f56;
    border-bottom: 1px solid #d8dde1;
    background: #eef0f3;
    user-select: none;
  }
  .{{ID}}-table {
    border-bottom: 1px solid #e7e9ec;
    font-size: 12px;
  }
  .{{ID}}-table-name {
    padding: 5px 10px;
    cursor: pointer;
    font-weight: 500;
    user-select: none;
  }
  .{{ID}}-table-name:hover { background: #e9ecef; }
  .{{ID}}-table-name::before {
    content: "▸ ";
    display: inline-block;
    width: 12px;
    color: #999;
  }
  .{{ID}}-table.open .{{ID}}-table-name::before { content: "▾ "; }
  .{{ID}}-cols {
    display: none;
    padding: 2px 0 6px 22px;
    font-size: 11px;
    color: #495057;
  }
  .{{ID}}-table.open .{{ID}}-cols { display: block; }
  .{{ID}}-col {
    padding: 2px 4px;
    cursor: pointer;
    border-radius: 3px;
  }
  .{{ID}}-col:hover { background: #d6e4f5; }
  .{{ID}}-col-type {
    color: #8b9298;
    font-style: italic;
    margin-left: 4px;
    font-size: 10px;
  }
  .{{ID}}-tnote {
    padding: 0 10px 4px 22px;
    font-size: 10px;
    color: #6c757d;
    font-style: italic;
  }
  .{{ID}}-right {
    flex: 1;
    display: flex;
    flex-direction: column;
    min-width: 0;
  }
  .{{ID}}-toolbar {
    padding: 5px 10px;
    background: #eef0f3;
    border-bottom: 1px solid #d8dde1;
    font-size: 11px;
    color: #4a4f56;
    user-select: none;
  }
  .{{ID}}-toolbar b { color: #1f2329; }
  .{{ID}}-toolbar button {
    background: #fff;
    border: 1px solid #c8ccd0;
    border-radius: 3px;
    padding: 2px 8px;
    margin-left: 6px;
    font-size: 11px;
    cursor: pointer;
  }
  .{{ID}}-toolbar button:hover { background: #f0f3f6; }
  .{{ID}}-editor-wrap {
    position: relative;
    flex: 1;
    background: #1e2126;
    overflow: hidden;
  }
  .{{ID}}-editor {
    width: 100%;
    height: 100%;
    background: #1e2126;
    color: #e6e6e6;
    border: none;
    padding: 12px;
    font-family: "SF Mono", Menlo, Consolas, monospace;
    font-size: 13px;
    line-height: 1.55;
    resize: none;
    outline: none;
    box-sizing: border-box;
    white-space: pre;
    tab-size: 2;
  }
  .{{ID}}-editor::placeholder { color: #6b7280; }
  .{{ID}}-popup {
    position: absolute;
    background: #fff;
    border: 1px solid #adb5bd;
    border-radius: 4px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.15);
    max-height: 220px;
    overflow-y: auto;
    z-index: 10;
    display: none;
    font-family: "SF Mono", Menlo, Consolas, monospace;
    font-size: 12px;
    min-width: 240px;
  }
  .{{ID}}-popup-item {
    padding: 4px 12px;
    cursor: pointer;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
    color: #1f2329;
  }
  .{{ID}}-popup-item.active { background: #2563eb; color: #fff; }
  .{{ID}}-popup-item .meta {
    font-size: 10px;
    opacity: 0.7;
    font-style: italic;
    flex-shrink: 0;
  }
  .{{ID}}-popup-item.active .meta { color: #cfd8e3; }
  .{{ID}}-bottom {
    padding: 5px 10px;
    background: #f7f8fa;
    border-top: 1px solid #d8dde1;
    font-size: 11px;
    color: #4a4f56;
    display: flex;
    align-items: center;
    gap: 12px;
    overflow-x: auto;
    white-space: nowrap;
    user-select: none;
    min-height: 22px;
  }
  .{{ID}}-bottom .label { color: #6c757d; }
  .{{ID}}-bottom .pill {
    display: inline-block;
    padding: 1px 8px;
    margin-right: 4px;
    background: #fff;
    border: 1px solid #c8ccd0;
    border-radius: 10px;
    font-family: "SF Mono", Menlo, Consolas, monospace;
    font-size: 11px;
    cursor: pointer;
  }
  .{{ID}}-bottom .pill:hover { background: #e9ecef; border-color: #9aa0a6; }
  .{{ID}}-bottom .pill.kw { color: #2563eb; }
  .{{ID}}-bottom .pill.tbl { color: #047857; }
  .{{ID}}-bottom .pill.col { color: #b45309; }
</style>

<div class="{{ID}}-root" id="{{ID}}">
  <div class="{{ID}}-left">
    <h3>📚 Entities</h3>
    <div id="{{ID}}-tree"></div>
  </div>
  <div class="{{ID}}-right">
    <div class="{{ID}}-toolbar">
      <b>SQL Editor</b> · 자동완성: <b>Ctrl+Space</b> · 들여쓰기: Tab · 컬럼 더블클릭 → 커서 삽입
      <button id="{{ID}}-btn-clear">지우기</button>
      <button id="{{ID}}-btn-copy">SQL 복사</button>
    </div>
    <div class="{{ID}}-editor-wrap">
      <textarea class="{{ID}}-editor"
                id="{{ID}}-editor"
                placeholder="SELECT * FROM ..."
                spellcheck="false"
                autocorrect="off"
                autocapitalize="off"></textarea>
      <div class="{{ID}}-popup" id="{{ID}}-popup"></div>
    </div>
    <div class="{{ID}}-bottom" id="{{ID}}-bottom"></div>
  </div>
</div>

<script>
(function() {
  const SCHEMA = {{SCHEMA_JSON}};
  const ID = "{{ID}}";
  const $ = (id) => document.getElementById(ID + "-" + id);
  const ROOT = document.getElementById(ID);
  const TREE = $("tree");
  const EDITOR = $("editor");
  const POPUP = $("popup");
  const BOTTOM = $("bottom");

  // ── 초기 쿼리 세팅 ──
  if (SCHEMA.initial_query) {
    EDITOR.value = SCHEMA.initial_query;
  }

  // ── 키워드 정의 ──
  const KEYWORDS = [
    "SELECT", "FROM", "WHERE", "AND", "OR", "NOT", "IN", "LIKE", "IS", "NULL",
    "JOIN", "INNER", "LEFT", "RIGHT", "FULL", "OUTER", "ON", "USING",
    "AS", "GROUP", "ORDER", "BY", "HAVING", "LIMIT", "OFFSET",
    "DISTINCT", "ALL", "UNION", "EXCEPT", "INTERSECT",
    "INSERT", "UPDATE", "DELETE", "INTO", "VALUES", "SET",
    "CREATE", "ALTER", "DROP", "TABLE", "INDEX", "VIEW", "WITH", "RECURSIVE",
    "CASE", "WHEN", "THEN", "ELSE", "END",
    "ASC", "DESC", "BETWEEN", "EXISTS",
    "TRUE", "FALSE",
  ];
  const FUNCTIONS = [
    "COUNT", "SUM", "AVG", "MIN", "MAX",
    "COALESCE", "NULLIF", "IFNULL",
    "UPPER", "LOWER", "LENGTH", "SUBSTR", "TRIM", "REPLACE",
    "ROUND", "FLOOR", "CEIL", "ABS",
    "DATE", "DATETIME", "STRFTIME", "JULIANDAY",
    "CAST",
  ];

  // ── 좌측 트리 렌더 ──
  function renderTree() {
    const tables = SCHEMA.tables || {};
    const notes = SCHEMA.notes || {};
    let html = "";
    const tnames = Object.keys(tables);
    if (tnames.length === 0) {
      html = `<div style="padding:12px;color:#888;font-size:11px">
        등록된 테이블이 없습니다.<br>
        Python 에서 <code>editor.add_table(...)</code> 로 추가하세요.
      </div>`;
    } else {
      for (const tname of tnames) {
        const cols = tables[tname] || [];
        const note = notes[tname] || "";
        html += `<div class="${ID}-table" data-table="${esc(tname)}">`;
        html += `<div class="${ID}-table-name">${esc(tname)}`;
        html += ` <span style="font-size:10px;color:#9aa0a6">(${cols.length})</span>`;
        html += `</div>`;
        if (note) {
          html += `<div class="${ID}-tnote">${esc(note)}</div>`;
        }
        html += `<div class="${ID}-cols">`;
        for (const c of cols) {
          const t = c.type ? `<span class="${ID}-col-type">${esc(c.type)}</span>` : "";
          html += `<div class="${ID}-col" data-col="${esc(c.name)}" data-table="${esc(tname)}">${esc(c.name)}${t}</div>`;
        }
        html += `</div></div>`;
      }
    }
    TREE.innerHTML = html;

    // 테이블 토글
    TREE.querySelectorAll("." + ID + "-table-name").forEach(el => {
      el.addEventListener("click", () => {
        el.parentElement.classList.toggle("open");
      });
      // 더블클릭 → 테이블 이름 삽입
      el.addEventListener("dblclick", (e) => {
        e.stopPropagation();
        const tname = el.parentElement.dataset.table;
        insertAtCursor(tname);
      });
    });
    // 컬럼 더블클릭 → 컬럼명 삽입
    TREE.querySelectorAll("." + ID + "-col").forEach(el => {
      el.addEventListener("dblclick", () => {
        insertAtCursor(el.dataset.col);
      });
    });
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function insertAtCursor(text) {
    const start = EDITOR.selectionStart;
    const end = EDITOR.selectionEnd;
    const v = EDITOR.value;
    EDITOR.value = v.substring(0, start) + text + v.substring(end);
    EDITOR.selectionStart = EDITOR.selectionEnd = start + text.length;
    EDITOR.focus();
    refresh();
  }

  // ── 컨텍스트 감지 ──
  function getCurrentWord() {
    const cursor = EDITOR.selectionStart;
    const v = EDITOR.value;
    let start = cursor;
    while (start > 0 && /[\w_.]/.test(v[start - 1])) start--;
    return { text: v.substring(start, cursor), start, end: cursor };
  }

  function detectContext() {
    const cursor = EDITOR.selectionStart;
    const before = EDITOR.value.substring(0, cursor);
    // 토큰화 — 단순 split (문자열 리터럴 무시 — 데모 수준)
    const tokens = before
      .replace(/'[^']*'/g, " ")
      .replace(/"[^"]*"/g, " ")
      .split(/\s+/)
      .filter(t => t.length > 0);

    if (tokens.length === 0) return "start";

    // 마지막 anchor 키워드 탐색 (역순)
    const ANCHORS = new Set([
      "SELECT", "FROM", "WHERE", "JOIN", "ON", "AND", "OR",
      "GROUP", "ORDER", "HAVING", "LIMIT", "BY",
      "INSERT", "UPDATE", "DELETE", "SET", "INTO", "VALUES",
      "INNER", "LEFT", "RIGHT", "FULL",
      "UNION", "EXCEPT", "INTERSECT",
      "AS", "WITH",
    ]);
    let lastAnchor = null;
    let lastAnchorIdx = -1;
    for (let i = tokens.length - 1; i >= 0; i--) {
      const tu = tokens[i].toUpperCase();
      if (ANCHORS.has(tu)) {
        lastAnchor = tu;
        lastAnchorIdx = i;
        break;
      }
    }
    if (lastAnchor === null) return "start";

    // GROUP BY / ORDER BY 합성
    if ((lastAnchor === "GROUP" || lastAnchor === "ORDER") &&
        lastAnchorIdx + 1 < tokens.length &&
        tokens[lastAnchorIdx + 1].toUpperCase() === "BY") {
      lastAnchor = lastAnchor + "_BY";
    }
    // BY 단독 (ex. "GROUP BY ") → 직전 키워드 확인
    if (lastAnchor === "BY" && lastAnchorIdx > 0) {
      const prev = tokens[lastAnchorIdx - 1].toUpperCase();
      if (prev === "GROUP" || prev === "ORDER") {
        lastAnchor = prev + "_BY";
      }
    }

    const MAP = {
      "SELECT": "columns_or_star",
      "FROM": "tables",
      "JOIN": "tables",
      "INNER": "join_continue",
      "LEFT": "join_continue",
      "RIGHT": "join_continue",
      "FULL": "join_continue",
      "ON": "columns",
      "WHERE": "columns",
      "AND": "columns",
      "OR": "columns",
      "GROUP_BY": "columns",
      "ORDER_BY": "columns",
      "HAVING": "columns",
      "LIMIT": "number",
      "INTO": "tables",
      "UPDATE": "tables",
      "DELETE": "from_keyword",
      "SET": "columns",
      "VALUES": "any",
      "AS": "any",
      "WITH": "any",
    };
    return MAP[lastAnchor] || "general";
  }

  function detectQualifier(word) {
    // "table." 형태 → 테이블의 컬럼만
    const dotIdx = word.indexOf(".");
    if (dotIdx > 0) {
      const tname = word.substring(0, dotIdx);
      if (SCHEMA.tables && SCHEMA.tables[tname]) {
        return { table: tname, prefix: word.substring(dotIdx + 1) };
      }
    }
    return null;
  }

  function getSuggestions() {
    const w = getCurrentWord();
    const filter = w.text.toLowerCase();

    // table_name. 형태 — 해당 테이블 컬럼만
    const q = detectQualifier(w.text);
    if (q) {
      const cols = (SCHEMA.tables[q.table] || []);
      const fp = q.prefix.toLowerCase();
      return cols
        .filter(c => c.name.toLowerCase().startsWith(fp))
        .map(c => ({
          value: q.table + "." + c.name,
          label: c.name,
          kind: "column",
          meta: c.type || q.table,
        }));
    }

    const ctx = detectContext();
    let cands = [];

    // tables
    if (ctx === "tables" || ctx === "general" || ctx === "start") {
      for (const tname of Object.keys(SCHEMA.tables || {})) {
        cands.push({ value: tname, label: tname, kind: "table", meta: "table" });
      }
    }
    // columns (전 테이블)
    if (ctx === "columns" || ctx === "columns_or_star" ||
        ctx === "general" || ctx === "start") {
      const seen = new Set();
      for (const [tname, cols] of Object.entries(SCHEMA.tables || {})) {
        for (const c of cols) {
          const key = c.name;
          if (!seen.has(key)) {
            cands.push({
              value: c.name,
              label: c.name,
              kind: "column",
              meta: (c.type ? c.type + " · " : "") + tname,
            });
            seen.add(key);
          }
        }
      }
    }
    // SELECT 직후엔 * 도 추천
    if (ctx === "columns_or_star") {
      cands.unshift({ value: "*", label: "*", kind: "star", meta: "all columns" });
    }
    // join_continue 일 땐 JOIN 키워드
    if (ctx === "join_continue") {
      ["JOIN", "OUTER JOIN"].forEach(k =>
        cands.push({ value: k, label: k, kind: "keyword", meta: "join" }));
    }
    // from_keyword 는 DELETE 다음 → FROM 추천
    if (ctx === "from_keyword") {
      cands.push({ value: "FROM", label: "FROM", kind: "keyword", meta: "kw" });
    }
    // 키워드 + 함수 (start/general 에서)
    if (ctx === "general" || ctx === "start") {
      for (const k of KEYWORDS) {
        cands.push({ value: k, label: k, kind: "keyword", meta: "kw" });
      }
      for (const f of FUNCTIONS) {
        cands.push({ value: f + "(", label: f + "(", kind: "function", meta: "fn" });
      }
    }
    if (ctx === "columns" || ctx === "columns_or_star") {
      for (const f of FUNCTIONS) {
        cands.push({ value: f + "(", label: f + "(", kind: "function", meta: "fn" });
      }
    }

    return cands.filter(c =>
      filter === "" || c.label.toLowerCase().includes(filter)
    );
  }

  // ── Popup ──
  let popupActive = -1;
  let popupItems = [];

  function applyCompletion(item) {
    if (!item) return;
    const w = getCurrentWord();
    const v = EDITOR.value;
    EDITOR.value = v.substring(0, w.start) + item.value + v.substring(w.end);
    EDITOR.selectionStart = EDITOR.selectionEnd = w.start + item.value.length;
    hidePopup();
    EDITOR.focus();
    refresh();
  }

  function hidePopup() {
    POPUP.style.display = "none";
    popupActive = -1;
    popupItems = [];
  }

  function showPopup() {
    const items = getSuggestions();
    popupItems = items.slice(0, 50);
    if (popupItems.length === 0) {
      hidePopup();
      return;
    }
    let html = "";
    popupItems.forEach((item, i) => {
      const cls = i === 0 ? "active" : "";
      html += `<div class="${ID}-popup-item ${cls}" data-i="${i}">`;
      html += `<span>${esc(item.label)}</span>`;
      html += `<span class="meta">${esc(item.meta || item.kind)}</span>`;
      html += `</div>`;
    });
    POPUP.innerHTML = html;
    POPUP.style.display = "block";
    popupActive = 0;
    positionPopup();
    POPUP.querySelectorAll("." + ID + "-popup-item").forEach(el => {
      el.addEventListener("click", () => {
        applyCompletion(popupItems[parseInt(el.dataset.i)]);
      });
      el.addEventListener("mouseenter", () => {
        popupActive = parseInt(el.dataset.i);
        renderActive();
      });
    });
  }

  function positionPopup() {
    // 커서 위치를 textarea 의 mono font 기준 (line, col) 로 추정
    const cursor = EDITOR.selectionStart;
    const before = EDITOR.value.substring(0, cursor);
    const lines = before.split("\n");
    const lineNum = lines.length - 1;
    const col = lines[lineNum].length;
    const styles = getComputedStyle(EDITOR);
    const lh = parseFloat(styles.lineHeight) || 20;
    // monospace 폭 추정 (13px font → 약 7.7px per char)
    const cw = parseFloat(styles.fontSize) * 0.6 || 7.8;
    const padL = parseFloat(styles.paddingLeft) || 12;
    const padT = parseFloat(styles.paddingTop) || 12;
    const wrapH = EDITOR.parentElement.clientHeight;
    let top = padT + (lineNum + 1) * lh - EDITOR.scrollTop + 4;
    let left = padL + col * cw - EDITOR.scrollLeft;
    // 화면 하단 넘으면 위로 띄움
    if (top + 200 > wrapH) top = padT + lineNum * lh - 200 - EDITOR.scrollTop;
    POPUP.style.top = Math.max(4, top) + "px";
    POPUP.style.left = Math.max(4, left) + "px";
  }

  function renderActive() {
    POPUP.querySelectorAll("." + ID + "-popup-item").forEach((el, i) => {
      el.classList.toggle("active", i === popupActive);
    });
    const act = POPUP.querySelector("." + ID + "-popup-item.active");
    if (act) act.scrollIntoView({ block: "nearest" });
  }

  // ── 하단 컨텍스트 패널 ──
  function renderBottom() {
    const w = getCurrentWord();
    const ctx = detectContext();
    const ctxLabels = {
      "start": "시작 — 어떤 키워드든",
      "tables": "테이블 추천",
      "columns": "컬럼 추천",
      "columns_or_star": "컬럼 또는 *",
      "join_continue": "JOIN/OUTER JOIN",
      "from_keyword": "FROM 추천",
      "number": "숫자 입력",
      "any": "임의 입력",
      "general": "범용",
    };
    let html = `<span class="label">컨텍스트:</span> <b>${esc(ctxLabels[ctx] || ctx)}</b>`;
    html += `<span class="label">| 추천:</span> `;
    const sugs = getSuggestions().slice(0, 12);
    if (sugs.length === 0) {
      html += `<span class="label" style="font-style:italic">(없음)</span>`;
    } else {
      for (const s of sugs) {
        const cls = (s.kind === "keyword" || s.kind === "function") ? "kw"
                  : (s.kind === "table" ? "tbl"
                  : (s.kind === "column" ? "col" : ""));
        html += `<span class="pill ${cls}" data-val="${esc(s.value)}">${esc(s.label)}</span>`;
      }
    }
    BOTTOM.innerHTML = html;
    BOTTOM.querySelectorAll(".pill").forEach(el => {
      el.addEventListener("click", () => {
        applyCompletion({ value: el.dataset.val });
      });
    });
  }

  function refresh() {
    renderBottom();
  }

  // ── 키 이벤트 ──
  EDITOR.addEventListener("input", () => {
    if (POPUP.style.display === "block" || autoTrigger()) {
      showPopup();
    }
    refresh();
  });
  EDITOR.addEventListener("click", refresh);
  EDITOR.addEventListener("keyup", (e) => {
    if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(e.key)) {
      refresh();
    }
  });

  function autoTrigger() {
    const w = getCurrentWord();
    return w.text.length >= 1;  // 1글자 이상이면 popup 자동 노출
  }

  EDITOR.addEventListener("keydown", (e) => {
    const popupOpen = POPUP.style.display === "block";
    if (popupOpen) {
      if (e.key === "ArrowDown") {
        popupActive = Math.min(popupItems.length - 1, popupActive + 1);
        renderActive();
        e.preventDefault();
        return;
      }
      if (e.key === "ArrowUp") {
        popupActive = Math.max(0, popupActive - 1);
        renderActive();
        e.preventDefault();
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        if (popupActive >= 0 && popupActive < popupItems.length) {
          applyCompletion(popupItems[popupActive]);
          e.preventDefault();
          return;
        }
      }
      if (e.key === "Escape") {
        hidePopup();
        e.preventDefault();
        return;
      }
    } else {
      if (e.key === "Tab") {
        // Tab 들여쓰기 (2 칸)
        const start = EDITOR.selectionStart;
        const end = EDITOR.selectionEnd;
        EDITOR.value = EDITOR.value.substring(0, start) + "  " + EDITOR.value.substring(end);
        EDITOR.selectionStart = EDITOR.selectionEnd = start + 2;
        e.preventDefault();
        refresh();
        return;
      }
      if ((e.ctrlKey || e.metaKey) && e.code === "Space") {
        // Ctrl+Space : 자동완성 강제 호출
        showPopup();
        e.preventDefault();
        return;
      }
    }
  });

  // 외부 클릭 시 popup 닫기
  document.addEventListener("click", (e) => {
    if (!ROOT.contains(e.target)) hidePopup();
  });

  // ── 툴바 버튼 ──
  $("btn-clear").addEventListener("click", () => {
    if (EDITOR.value.length > 0 &&
        !confirm("에디터 내용을 모두 지울까요?")) return;
    EDITOR.value = "";
    refresh();
    EDITOR.focus();
  });

  $("btn-copy").addEventListener("click", async () => {
    const text = EDITOR.value;
    try {
      await navigator.clipboard.writeText(text);
      flashStatus("✓ 클립보드에 복사됨");
    } catch (err) {
      // 폐쇄망/제한 환경 fallback — textarea 선택만
      EDITOR.select();
      flashStatus("⚠ clipboard API 차단 — 수동으로 Cmd/Ctrl+C 해주세요");
    }
  });

  function flashStatus(msg) {
    const orig = BOTTOM.innerHTML;
    BOTTOM.innerHTML = `<b style="color:#047857">${esc(msg)}</b>`;
    setTimeout(() => { refresh(); }, 1800);
  }

  // ── 초기 렌더 ──
  renderTree();
  refresh();
  EDITOR.focus();
})();
</script>
"""


# ===== 3. __main__ — 라이브러리 안내 =====

if __name__ == "__main__":
    print(
        "이 파일은 라이브러리입니다. 사용 예시는 basic_usage.py 또는\n"
        "demo.ipynb 를 참고하세요."
    )
