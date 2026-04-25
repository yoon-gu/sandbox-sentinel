"""
SQL Runner with CodeMirror inline (single-file, 폐쇄망 친화).

005/006 와의 관계
----------------
  · 005 = HTML/JS 단독 SQL 편집기 (popup 자동완성, Python 콜백 없음)
  · 006 = ipywidgets Textarea 기반 (실행 가능 + 라이브 강조 별도 프리뷰)
  · 007 = **CodeMirror 5.65.16 인라인 임베드** — 에디터 자체에 syntax
          highlight 색이 입혀지고, popup 자동완성도 inline 으로 동작.
          ▶ 실행 버튼으로 Python 콜백 호출 (006 와 동일).

포지셔닝 한 줄: "005 의 자유도 + 006 의 실행성 + 진짜 IDE 같은 에디터 체감"

라이선스: MIT (CodeMirror) + MIT (오리지널 wrapper)
생성: Code Conversion Agent

핵심 기능
--------
  1) 좌측 entity 트리 — 005/006 와 동일 API (add_table / from_dict /
     from_sqlite / from_dataframes), 클릭 시 에디터 커서 위치에 정확히 인서트
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


# ===== 컨텍스트 감지 + 추천 (Python 사이드 — 005/006 동일 골격) =====
# CM 안의 popup 자동완성은 JS 사이드에서 contextHint() 가 처리.
# 여기 Python 함수들은 에디터 아래에 늘 띄워두는 칩 패널 (=005 의 추천
# 영역) 을 ipywidgets.Button 으로 그릴 때 사용한다. cursor 위치를 모르므로
# 005 와 달리 "텍스트 끝" 을 기준으로 동작 (006 와 동일 한계).

_ANCHORS = {
    "SELECT", "FROM", "WHERE", "JOIN", "ON", "AND", "OR",
    "GROUP", "ORDER", "HAVING", "LIMIT", "BY",
    "INSERT", "UPDATE", "DELETE", "SET", "INTO", "VALUES",
    "INNER", "LEFT", "RIGHT", "FULL",
    "UNION", "EXCEPT", "INTERSECT",
    "AS", "WITH",
}


def detect_context(text: str) -> str:
    """직전 anchor 키워드로 추천 종류를 결정 (005 와 동일 정책)."""
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


def get_suggestions(text: str, tables: Mapping[str, list]) -> list:
    """현재 컨텍스트에 맞는 추천 후보 리스트 (텍스트 끝 기준)."""
    ctx = detect_context(text)
    m = re.search(r"([\w_.]+)$", text)
    last_word = m.group(1) if m else ""
    last_lower = last_word.lower()

    # table. qualifier 우선
    if "." in last_word:
        dot_idx = last_word.index(".")
        tname = last_word[:dot_idx]
        col_prefix = last_word[dot_idx + 1:].lower()
        if tname in tables:
            return [
                {
                    "value": f"{tname}.{c['name']}",   # 실제 인서트 텍스트
                    # 표시 라벨 — 타입을 괄호로 함께 노출 ("id (INT)")
                    "label": (f"{c['name']} ({c.get('type','')})"
                              if c.get("type") else c["name"]),
                    "kind": "column",
                    "meta": c.get("type", "") or tname,
                }
                for c in tables[tname]
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
                # 추천 표시 라벨에 (TYPE) 동시 노출
                col_label = f"{c['name']} ({type_str})" if type_str else c["name"]
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

    // 식별자 입력 중일 때 자동 popup
    cm.on("inputRead", function(cm, change){{
      if(change && change.text && change.text.length === 1){{
        var t = change.text[0];
        if(/^[A-Za-z_.]$/.test(t)){{
          cm.showHint({{ hint: contextHint, completeSingle: false }});
        }}
      }}
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
    var last = null, lastIdx = -1;
    for(var i = tokens.length-1; i >= 0; i--){{
      var tu = tokens[i].toUpperCase();
      if(ANCHORS[tu]){{ last = tu; lastIdx = i; break; }}
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
    var start = cur.ch, end = cur.ch;
    while(start > 0 && /[\w_.]/.test(line[start-1])) start--;
    var word = line.substring(start, end);
    var beforeAll = cm.getRange({{line:0,ch:0}}, cur);
    var ctx = detectContext(beforeAll);

    // table. qualifier 우선 처리
    var dot = word.indexOf(".");
    if(dot > 0){{
      var tname = word.substring(0, dot);
      var fp = word.substring(dot+1).toLowerCase();
      if(SCHEMA[tname]){{
        var list = SCHEMA[tname]
          .filter(function(c){{ return c.name.toLowerCase().indexOf(fp) === 0; }})
          .map(function(c){{
            // 표시 라벨에 타입을 괄호로 동시 노출 ("id (INT)")
            var disp = c.type
              ? (c.name + " (" + c.type + ")")
              : c.name;
            return {{ text: tname + "." + c.name, displayText: disp }};
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
          // 컬럼 추천 표시: "id (INT)  · users" 형태로 타입 괄호 노출
          var disp = c.type
            ? (c.name + " (" + c.type + ")  · " + tname)
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
                 on_execute: Optional[Callable[[str], Any]] = None) -> None:
        self.tables: dict[str, list[dict]] = {}
        self.notes: dict[str, str] = {}
        self.initial_query: str = ""
        self.on_execute = on_execute

        # ── 후속 분석을 위한 실행 상태 ──
        # ▶ 실행 후 다음 셀에서 runner.last_result.head() 같이 접근 가능.
        self.last_query: Optional[str] = None      # 마지막으로 실행한 SQL
        self.last_result: Any = None               # 마지막 실행의 반환값
        self.last_error: Optional[BaseException] = None  # 실패했다면 예외
        self.history: list[dict] = []              # [{query, result, error}]

        self._textarea = None
        self._cursor_text = None    # CM cursor 위치까지의 텍스트 (cursorActivity 동기화용)
        self._run_box = None
        self._output = None
        self._suggest_box = None
        self._uid = "u" + uuid.uuid4().hex[:10]

    @property
    def query(self) -> str:
        """현재 에디터에 작성된 SQL (▶ 실행 안 했어도 읽기 가능)."""
        return self._textarea.value if self._textarea is not None else self.initial_query

    @property
    def result(self) -> Any:
        """last_result 의 짧은 alias — runner.result 로 바로 접근."""
        return self.last_result

    # ----- 편의 생성자 (006 의 with_sqlite 와 동일 패턴) -----

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

    # ----- 스키마 등록 (005/006 와 동일 API) -----

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

        # ── 좌측 entity 트리 ──
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
                '<code>add_table(...)</code> / <code>from_dict(...)</code> '
                '로 추가하세요.</div>'
            ))
        else:
            for tname, cols in self.tables.items():
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

                # ── 컬럼: 컴팩트한 wrap-flex Button 칩 + 호버 시 doc 만 노출
                # 컬럼명은 칩 자체에 표시되니 tooltip 에 중복할 필요 없고,
                # type 도 자동완성 추천 popup 에서 (TYPE) 으로 보여주므로 호버
                # 에는 description (doc) 만. doc 가 없는 컬럼은 tooltip 미노출.
                col_btns: list = []
                for c in cols:
                    tooltip = c.get("doc", "") or ""
                    cbtn = W.Button(
                        description=c["name"],
                        tooltip=tooltip,
                        layout=W.Layout(margin="1px", width="auto",
                                        height="22px"),
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
        run_btn = W.Button(description="▶ 실행 (Cmd/Ctrl+Enter)",
                           button_style="primary",
                           layout=W.Layout(width="auto"))
        copy_btn = W.Button(description="📋 SQL 복사",
                            layout=W.Layout(width="auto"))
        clear_btn = W.Button(description="🗑 지우기",
                             layout=W.Layout(width="auto"))
        run_btn.on_click(self._on_run)
        copy_btn.on_click(self._on_copy)
        clear_btn.on_click(self._on_clear)

        self._run_box = W.HBox([run_btn], layout=W.Layout(padding="0"))
        self._run_box.add_class(f"cm-run-{self._uid}")
        actions = W.HBox(
            [self._run_box, copy_btn, clear_btn],
            layout=W.Layout(padding="4px 0"),
        )

        # ── 추천 칩 패널 (005 와 동일 컨셉) ──
        # CM popup 자동완성과 별개로 항상 보이는 컨텍스트 추천. cursor 위치를
        # 알 수 없어 텍스트 끝 기준으로 동작 — 정밀도가 popup 보다 낮은
        # 대신 사용자가 "지금 뭘 칠 수 있는지" 한눈에 보이는 장점이 있음.
        self._suggest_box = W.HBox(
            layout=W.Layout(flex_flow="row wrap", padding="2px 0",
                            min_height="32px"),
        )

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
                '· runner.last_result / runner.history 로 후속 분석 가능'
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

    def _on_run(self, _btn: Any) -> None:
        from IPython.display import display
        sql = self._textarea.value if self._textarea is not None else ""
        # last_query 는 빈 SQL 이라도 일단 기록 (사용자가 디버깅 시 도움)
        self.last_query = sql
        with self._output:
            self._output.clear_output()
            if not sql.strip():
                print("⚠ SQL 이 비어있습니다.")
                return
            if self.on_execute is None:
                print("on_execute 콜백이 등록되지 않았습니다.")
                print("SQLRunnerCM(on_execute=lambda sql: pd.read_sql(sql, conn))")
                print("처럼 콜백을 주입하면 ▶ 실행 시 호출됩니다.\n")
                print(f"SQL:\n{sql}")
                return
            try:
                result = self.on_execute(sql)
            except Exception as e:
                import traceback
                self.last_error = e
                self.last_result = None
                self.history.append({"query": sql, "result": None,
                                      "error": e})
                print(f"❌ {type(e).__name__}: {e}")
                traceback.print_exc()
                return
            self.last_error = None
            self.last_result = result
            self.history.append({"query": sql, "result": result,
                                  "error": None})
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

    def _on_copy(self, _btn: Any) -> None:
        from IPython.display import display, HTML
        sql_js = (
            (self._textarea.value if self._textarea is not None else "")
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
                "()=>{},()=>{alert('clipboard 차단 — 수동 복사 필요');}"
                ");}else{alert('clipboard API 미지원');}"
                "})();</script>"
                '<div style="padding:4px 8px;color:#047857;font-size:12px">'
                '✓ 클립보드에 복사 시도됨</div>'
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
