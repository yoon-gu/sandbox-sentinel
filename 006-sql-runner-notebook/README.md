# 006 - SQL Runner for Notebook (ipywidgets · 실행 가능)

> **한 줄 요약**: Jupyter 셀 안에서 SQL 을 작성하고 **▶ 실행 버튼으로 사용자 콜백을 호출** 해 결과를 바로 확인하는 single-file 위젯. 라이브 구문 강조 + 컨텍스트 추천 포함.

## 005 와의 관계 / 선택 가이드

[005-sql-editor-notebook](../005-sql-editor-notebook/) 와 같은 컨셉이지만 **다른 접근**:

| 항목 | 005 (HTML/JS only) | **006 (ipywidgets)** |
|---|---|---|
| 인라인 popup 자동완성 | ✅ 커서 위치 floating | ❌ (대신 항상 보이는 Button 칩) |
| 커서 위치 정밀 인서트 | ✅ | ❌ (마지막 부분 단어 치환 또는 끝 append) |
| **▶ 실행 → Python 콜백** | ❌ (JS-side 격리) | ✅ on_execute(sql) |
| **라이브 SQL 구문 강조** | ❌ | ✅ Python 미니 lexer 로 컬러 프리뷰 |
| 결과 표시 | (사용자가 다른 셀에서 실행) | ✅ Output 위젯에 DataFrame 등 자동 렌더 |
| 의존성 | IPython 만 | ipywidgets + IPython |

**언제 005 를 쓰나** — 자동완성 popup 의 인라인 자유도를 우선시할 때, 또는 ipywidgets 가 미설치 환경.
**언제 006 를 쓰나** — 즉시 실행/결과 확인이 중요할 때, 라이브 syntax highlighting 이 필요할 때.

## 원본 출처

| 항목 | 값 |
|---|---|
| 라이브러리 | (오리지널 구현 — 005 의 컨셉을 ipywidgets 기반으로 재구성) |
| 라이선스 | MIT |

## 기능 요약

- **`SQLRunner(on_execute=fn).show()` 한 줄로 실행 가능 위젯 렌더**
- **레이아웃** (HBox 좌·우 분할):
  - 좌: 📚 Entity 트리 — 테이블 헤더 + 컬럼 모두 `ipywidgets.Button`. 클릭 → 마지막 부분 단어를 똑똑하게 치환 또는 끝에 append
  - 우: SQL Runner 패널
    - `Textarea` 에디터 (Enter = newline 자연스럽게)
    - 🎨 라이브 구문 강조 프리뷰 (Python 미니 lexer · 외부 의존 없음)
    - 💡 컨텍스트 인식 추천 패널 (현재 anchor 키워드 표시 + 클릭 가능 Button 칩)
    - **`▶ 실행`** / `📋 SQL 복사` / `🗑 지우기` 버튼
    - 📤 Output 위젯에 결과 표시 (DataFrame 자동 표 렌더)
- **컨텍스트 인식 추천** (005 와 동일한 정책을 Python 으로):
  - `FROM` / `JOIN` 다음 → 테이블
  - `SELECT` 다음 → 컬럼 + `*` + 함수
  - `WHERE` / `AND` / `OR` / `ON` / `GROUP BY` / `ORDER BY` / `HAVING` 다음 → 컬럼
  - `table_name.` 입력 시 → 해당 테이블 컬럼만 한정
  - 시작/모호 → 키워드 + 함수 + 테이블 + 컬럼 종합
- **라이브 구문 강조** — 키워드(파랑) · 함수(연노랑) · 문자열(주황) · 숫자(연두) · 주석(녹색) · 식별자(연회색). textarea 변경 시 즉시 갱신.

## 의존성

| 사용 | 패키지 | 용도 |
|---|---|---|
| 필수 | `ipywidgets` | 위젯 framework |
| 필수 | `IPython` | display 통합 |
| 선택 | `pandas` | `on_execute=lambda sql: pd.read_sql(sql, conn)` 패턴 시 |
| 전이 | (없음) | sqlite3 stdlib · 외부 의존 추가 0 |

## 사용 예시

### 빠른 시작 (SQLite + pandas) — 편의 메서드

```python
from sql_runner import SQLRunner

runner = SQLRunner.with_sqlite("./demo.db")    # ⭐ thread-safe 자동 처리
runner.set_query("SELECT * FROM users LIMIT 10;")
runner.show()
```

`with_sqlite()` 는 매 ▶ 실행 마다 새 connection 을 열고 닫아 ipywidgets 의
스레드 이슈를 자동 회피합니다. (pandas 필요)

### 직접 콜백 — 공유 connection 패턴

```python
import sqlite3, pandas as pd
from sql_runner import SQLRunner

# ⚠️ ipywidgets 버튼 콜백은 다른 스레드에서 실행되므로 check_same_thread=False 필요
conn = sqlite3.connect("./demo.db", check_same_thread=False)

runner = SQLRunner(on_execute=lambda sql: pd.read_sql(sql, conn))
runner.from_sqlite("./demo.db")
runner.show()
```

또는 매 호출마다 connect (제일 안전):

```python
def run_sql(sql):
    with sqlite3.connect("./demo.db") as conn:
        return pd.read_sql(sql, conn)

runner = SQLRunner(on_execute=run_sql)
runner.from_sqlite("./demo.db")
runner.show()
```

> ⚠️ **SQLite + ipywidgets threading**: ▶ 실행 콜백은 Jupyter 커널의 IO 스레드
> 에서 발화되어 cell 의 메인 실행 스레드와 다릅니다. 평소처럼 `sqlite3.connect(path)`
> 한 connection 을 그대로 캡처하면 `ProgrammingError: SQLite objects created
> in a thread can only be used in that same thread` 가 발생합니다. 위 셋
> 패턴 중 하나 사용 권장 — 가장 간단한 건 `SQLRunner.with_sqlite(path)`.

### 직접 등록

```python
from sql_runner import SQLRunner

runner = SQLRunner(on_execute=my_executor)
runner.add_table("users", ["id", "name", "email"], description="사용자 마스터")
runner.add_table("orders", [
    ("id", "INT"),
    ("user_id", "INT"),
    ("amount", "REAL"),
    ("status", "TEXT"),
])
runner.show()
```

### `on_execute` 콜백 시그니처

```python
def on_execute(sql: str) -> Any:
    # sql 은 textarea 의 현재 텍스트 그대로 (주석/세미콜론 포함 가능)
    # 반환값 None → "실행 완료 (반환값 없음)" 만 표시
    # 반환값 있으면 IPython.display(value) 로 Output 위젯에 렌더
    return pd.read_sql(sql, my_connection)   # DataFrame 자동 표 렌더
```

콜백을 등록하지 않으면 ▶ 실행 클릭 시 안내 메시지 + SQL 출력만 표시 (시연용).

### 클립보드 복사

`📋 SQL 복사` 버튼은 브라우저의 `navigator.clipboard.writeText()` 를 trigger 합니다. clipboard 가 차단되면 alert 로 안내 (수동 Cmd/Ctrl+C 필요).

## 파일 구조

```
006-sql-runner-notebook/
├── README.md
├── sql_runner.py           # single-file (~700줄: SQLRunner + 미니 lexer + 추천 로직 + ipywidgets 합성)
├── metadata.json
├── LICENSE                 # MIT
└── examples/
    └── demo.ipynb          # 노트북 데모 (실행 가능 시나리오 포함)
```

## 폐쇄망 친화 체크

| 항목 | 상태 |
|---|---|
| 외부 네트워크 / CDN | ❌ 없음 (모든 CSS/JS 인라인) |
| 새 서버 / 포트 | ❌ 없음 |
| 바이너리 영속화 | ❌ 없음 (메모리 + 텍스트) |
| 단일 반입 단위 | `sql_runner.py` 한 파일 |
| 추가 패키지 | ipywidgets + IPython (이미 스택 포함) |

## 알려진 제약 / 한계

- **Inline popup autocomplete 없음** — Textarea 의 cursor 위치를 ipywidgets 가 노출하지 않아 floating popup 구현 불가. 대안으로 항상 노출되는 Button 칩으로 추천 표시. 인라인 popup 이 필수면 005 사용.
- **커서 위치 인서트 불가** — Button 클릭 시 텍스트 끝에 append (또는 마지막 부분 단어 치환). 텍스트 중간에 커서를 두고 클릭해도 끝에 추가됨.
- **구문 강조는 별도 프리뷰** — Textarea 자체에는 색이 안 입혀짐 (다크 monospace 까지만). 인라인 컬러 에디팅이 필요하면 CodeMirror/Monaco 등 외부 라이브러리 임베드 필요.
- **CTE/서브쿼리 경계 부정확** — 간이 토큰 분리라 가장 가까운 anchor 키워드만 봄. 복잡한 쿼리에선 추천이 어색할 수 있음.
- **Jupyter 신뢰 정책** — `📋 SQL 복사` 버튼이 navigator.clipboard 를 호출하는 JS 한 조각을 일회성 display 로 발행. 신뢰가 차단된 환경에선 동작하지 않을 수 있음 (이 경우 textarea 에서 수동 Cmd/Ctrl+C 권장).
