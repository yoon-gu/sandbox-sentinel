# 008 - SQL Runner TUI (Textual · 터미널 풀스크린 · 실행 가능)

> **한 줄 요약**: 터미널 풀스크린에서 동작하는 single-file SQL 편집기 + 실행기. Textual TextArea 의 native SQL syntax highlight 로 **에디터 자체에 색이 입혀지고**, Ctrl+Space 자동완성 popup, Ctrl+R 실행, DataTable 결과 — 모두 터미널 안에서.

## 005 / 006 / 007 / 008 한눈에 비교

| 항목 | 005 (HTML/JS) | 006 (ipywidgets) | 007 (CodeMirror) | **008 (Textual TUI)** |
|---|---|---|---|---|
| 환경 | Jupyter 노트북 | Jupyter 노트북 | Jupyter 노트북 | **터미널** (ssh OK) |
| 브라우저 / Trust 필요 | ✅ / (script 실행) | ✅ / 위젯만 | ✅ / **Trust 필수** | ❌ |
| **에디터 자체** syntax 색 | ❌ | ❌ (별도 미리보기) | ✅ CodeMirror | ✅ Textual native (tree-sitter SQL 번들) |
| inline popup 자동완성 | ✅ floating | ❌ Button 칩 | ✅ Ctrl+Space | ✅ Ctrl+Space (ModalScreen) |
| 컨텍스트 추천 칩 | ✅ | ✅ | ✅ | ✅ (하단 라인) |
| 커서 위치 정밀 인서트 | ✅ | ❌ 끝 append | ✅ | ✅ (마지막 부분 단어 치환) |
| ▶ 실행 → Python 콜백 | ❌ | ✅ | ✅ | ✅ Ctrl+R / F5 |
| 결과 자동 표 렌더 | ❌ | ✅ pandas HTML | ✅ pandas HTML | ✅ Textual DataTable |
| 단축키 실행 | ❌ | ❌ | ✅ Cmd/Ctrl+Enter | ✅ Ctrl+R / F5 |
| 의존성 | IPython | ipywidgets+IPython | ipywidgets+IPython | **textual + rich** |
| 파일 크기 | ~30KB | ~30KB | ~280KB | **~25KB** |

**언제 008 을 쓰나** — ssh / 원격 터미널에서 일할 때, JupyterLab 띄우기 부담스러울 때, Trust 정책으로 인라인 `<script>` 가 막힌 환경, 가장 가벼운 단일 파일을 원할 때.
**여전히 노트북이 좋을 때** — 결과를 다음 셀로 넘겨 후속 분석을 이어가야 할 때 (008 은 결과를 DataTable 안에서만 봄), pandas DataFrame 의 풍부한 HTML repr 가 필요할 때.

## 원본 출처

| 항목 | 값 |
|---|---|
| TUI 프레임워크 | [Textual](https://github.com/Textualize/textual) (MIT) |
| 사용 버전 | 6.11.0 (스택 핀) |
| 라이선스 | MIT (`LICENSE` 참조) |
| 기타 | 오리지널 wrapper · 005~007 와 동일 컨셉의 TUI 포트 |

## 기능 요약

- **`SQLRunnerTUI(on_execute=fn).run()` 한 줄로 풀스크린 진입**
- **레이아웃**:
  - 좌: 📚 Entity Tree — 테이블/컬럼 트리. ↑↓ 이동, Enter → 에디터 커서 위치에 인서트
  - 우:
    - **Textual TextArea** — `language="sql"` native syntax highlight (라인 번호, soft wrap, 자동 들여쓰기, undo/redo)
    - **💡 인라인 추천 OptionList** — 에디터 바로 아래 항상 보이는 컨텍스트 추천. **편집 중 자동 갱신** · popup 모달 아님
    - **결과 DataTable** — DataFrame / list[dict] / list[tuple] 자동 표 변환
- **컨텍스트 인식 자동완성** (005~007 와 동일 정책):
  - `FROM` / `JOIN` 다음 → 테이블
  - `SELECT` 다음 → 컬럼 + `*` + 함수
  - `WHERE` / `AND` / `GROUP BY` / `ORDER BY` 다음 → 컬럼
  - `table_name.` 입력 시 → 해당 테이블 컬럼만 한정
  - **fallback** — `WHE`, `GR`, `JOI` 같은 부분 입력은 어느 컨텍스트에서나 KEYWORDS 매치

## 단축키

| 키 | 동작 |
|---|---|
| **Tab** | 에디터 ↔ 인라인 추천 리스트 포커스 토글 |
| **Ctrl+R / F5** | ▶ 실행 (현재 SQL 을 `on_execute` 에 전달) |
| **Ctrl+T** | 트리 포커스 |
| **Ctrl+E** | 에디터 포커스 |
| **Ctrl+L** | 에디터 비우기 |
| **F1** | 도움말 |
| **Ctrl+Q** | 종료 |
| 트리 ↑↓ Enter | 테이블/컬럼 이름을 현재 커서 위치에 인서트 |

### 인라인 추천 리스트 (편집 중 자동 갱신)

에디터 바로 아래에 **항상 보이는** OptionList — 입력에 따라 자동으로 후보가 갱신됩니다 (popup 모달이 아니므로 입력 흐름이 끊기지 않음).

- 에디터에서 [`Tab`] → 추천 리스트로 포커스 이동 (첫 항목 자동 하이라이트)
- 리스트에서 `↑↓` → 이동
- `Enter` → 선택 → 에디터 커서 위치에 인서트 + 자동으로 에디터 복귀
- `Esc` 또는 `Tab` → 선택 없이 에디터로 복귀

## 의존성

| 사용 | 패키지 | 용도 |
|---|---|---|
| 필수 | `textual>=6.11.0` | TUI 프레임워크 |
| 전이 | `rich`, `pygments`, `markdown_it_py`, `platformdirs` | textual 의 transitive 의존 |
| **SQL 색** | `tree-sitter`, `tree-sitter-sql` | 에디터 자체 inline syntax highlight ([상세](#syntax-highlight-요구-사항)) |
| 선택 | `pandas` | `with_sqlite()` / DataFrame 결과 자동 표 변환 시 |
| 선택 | `sqlite3` (stdlib) | `from_sqlite` / `with_sqlite` |

### Syntax highlight 요구 사항

Textual 6.x 의 TextArea 는 `language="sql"` 인자만으로는 highlight 가 동작하지 않습니다 — 실제 그래머 패키지가 별도로 필요합니다.

```bash
pip install tree-sitter tree-sitter-sql
```

설치되면 `_set_document` 시점에 `SyntaxAwareDocument` 가 생성되어 키워드 / 문자열 / 함수 / 숫자가 monokai 색으로 입혀집니다. 미설치 시 `LanguageDoesNotExist` 가 발생할 수 있어 sql_tui.py 는 try/except 로 plain text fallback 합니다 (오류 없이 단순히 색이 안 입혀짐).

> **폐쇄망 스택 정책**: textual 6.11.0 은 `--no-deps` 옵션으로 설치되어야 함 (`environment-adapter` Skill 의 `default.yaml` 참고). 실제 실행에는 `rich` 등이 필요하므로 사내 미러에서 함께 반입. tree-sitter 패키지도 사내 미러에 등록되어 있는 경우에만 syntax highlight 활성화.

## 사용 예시

### 빠른 시작 — `with_sqlite` 편의 메서드

```python
from sql_tui import SQLRunnerTUI

runner = SQLRunnerTUI.with_sqlite("./demo.db")
runner.set_query("SELECT * FROM users LIMIT 10;")
runner.run()    # 풀스크린 TUI 진입
```

### 직접 콜백 — 임의의 SQL 백엔드 연동

```python
import pandas as pd, sqlite3
from sql_tui import SQLRunnerTUI

# 사례 A — pandas + sqlite
runner = SQLRunnerTUI(
    on_execute=lambda sql: pd.read_sql(sql, sqlite3.connect("./demo.db"))
)
runner.from_sqlite("./demo.db").run()

# 사례 B — sqlite raw cursor (pandas 없이) — list[dict] 자동 표 렌더
def run_sql_raw(sql: str):
    with sqlite3.connect("./demo.db") as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql).fetchall()]

SQLRunnerTUI(on_execute=run_sql_raw).from_sqlite("./demo.db").run()

# 사례 C — REST API / 사내 엔진
def run_via_api(sql: str):
    import requests   # 폐쇄망 허용 패키지인 경우만
    r = requests.post("http://internal.api/sql", json={"sql": sql})
    return r.json()["rows"]

SQLRunnerTUI(on_execute=run_via_api).from_dict({
    "logs": ["id", "ts", "level", "message"],
}).run()
```

### 풍부한 컬럼 description

```python
runner.add_table("users", [
    {"name": "id",        "type": "INTEGER",   "doc": "PK"},
    {"name": "name",      "type": "TEXT",      "doc": "표시 이름"},
    {"name": "plan_type", "type": "TEXT",      "doc": "free/pro/enterprise"},
    {"name": "signup_at", "type": "TIMESTAMP", "doc": "가입 시각 (UTC)"},
], description="사용자 마스터")
```

`doc` 은 트리 leaf 옆에 dim 색으로 노출되고, 자동완성 popup 의 meta 텍스트에 사용됨.

## 파일 구조

```
008-sql-tui-runner/
├── README.md
├── sql_tui.py             # ⭐ single-file (~25KB)
├── metadata.json
├── LICENSE                # MIT (Textual 라이선스 명시)
└── examples/
    └── basic_usage.py     # CLI --check 검증 + 풀스크린 TUI 데모
```

## 실행 방법

```bash
# CLI 단위 검증 (TUI 띄우지 않고 detect_context/get_suggestions 만)
python examples/basic_usage.py --check

# 풀스크린 TUI 진입 (4 테이블, ~5명 사용자, 6건 주문 데모)
python examples/basic_usage.py

# 또는 sql_tui.py 자체에 들어있는 단순 데모
python sql_tui.py
```

## 폐쇄망 친화 체크

| 항목 | 상태 |
|---|---|
| 외부 네트워크 / CDN | ❌ 없음 |
| `<link href>` / `<script src>` | N/A (TUI · HTML 없음) |
| 새 서버 / 포트 | ❌ 없음 |
| 바이너리 영속화 | ❌ 없음 |
| 단일 반입 단위 | `sql_tui.py` 한 파일 |
| 추가 패키지 | textual + rich (이미 스택 포함) |

## 알려진 제약 / 한계

- **결과 후속 분석이 어려움** — DataTable 에 표시된 결과를 별도 셀로 넘기기 어려움. 이게 필요하면 006/007 (노트북) 사용 권장.
- **터미널 너비 제약** — 컬럼이 많으면 가로 스크롤이 필요. Textual DataTable 은 가로 스크롤 지원하지만 폰트가 좁은 환경에서 가독성 떨어짐.
- **마우스 동작 환경 의존** — JupyterLab 의 터미널, ssh 터미널, iTerm2/Terminal.app 등 환경에 따라 마우스 클릭 동작이 다를 수 있음. 키보드만으로 모든 조작 가능하니 마우스가 안 되면 단축키 사용.
- **textual native SQL highlight** — Textual 6.11.0 의 TextArea 는 SQL 을 native bundled language 로 지원 (tree-sitter SQL 그래머 포함). 만약 stripped 빌드라 SQL 이 빠지면 plain text 로 fall back 됨.
- **Korean (CJK) cursor 위치** — TextArea 가 wide character 의 cursor 위치를 정확히 잡지만, 일부 터미널 폰트에서 시각적으로 어긋날 수 있음.
- **CTE / 서브쿼리 경계 부정확** — 005~007 와 동일하게 간이 토큰 분리.
