# 005 - SQL Editor for Notebook

> **한 줄 요약**: Jupyter 셀 안에서 바로 동작하는 single-file SQL 편집기. 좌측 entity 트리 + 우측 쿼리 입력 + 컨텍스트 인식 자동완성·제안 — 외부 CDN/스크립트 0.

## 원본 출처

| 항목 | 값 |
|---|---|
| 라이브러리 | (오리지널 구현) |
| 컨셉 영감 | Snowflake Worksheet · BigQuery 콘솔 · DBeaver — UX 패턴만 참고, 코드 복제 아님 |
| 라이선스 | MIT |

## 기능 요약

- **`SQLEditor.show()` 한 줄로 셀에 위젯 렌더** — `IPython.display.HTML` 로 self-contained HTML+CSS+JS 임베드. 외부 CDN/스크립트 의존 0.
- **레이아웃** (480px 높이 · 1100px 최대 폭):
  - 좌측 (220px): **Entity 트리** — 등록된 테이블 + 컬럼 + 타입. ▸ 토글 펼침. 컬럼/테이블명 **더블클릭 → 커서 위치 삽입**.
  - 우측 (flex): **다크 테마 textarea 에디터** — monospace, Tab=2칸 들여쓰기, 한글 입력 OK.
  - 우측 하단: 항상 노출되는 **컨텍스트 패널** — 현재 위치에서 추천되는 키워드/테이블/컬럼 12개 표시, 클릭 가능 (pill).
- **컨텍스트 인식 자동완성**:
  - `FROM` / `JOIN` 다음 → **테이블**
  - `SELECT` 다음 → **컬럼 + `*` + 함수**
  - `WHERE` / `AND` / `OR` / `ON` / `HAVING` 다음 → **컬럼**
  - `GROUP BY` / `ORDER BY` 다음 → **컬럼**
  - **`table_name.`** 입력 시 → 해당 테이블의 컬럼만 한정 추천
  - 그 외 (시작 또는 분기 모호) → 키워드 + 함수 + 테이블 + 컬럼 종합
- **단축키**:
  - 1글자 이상 입력 → **자동 popup**
  - `Ctrl+Space` → **강제 popup**
  - `↑/↓` 항목 이동 · `Enter` 또는 `Tab` 확정 · `Esc` 닫기
  - 마우스: pill 또는 popup 항목 클릭으로 삽입
- **툴바 버튼**: `지우기` / `SQL 복사 (clipboard API)` — 폐쇄망에서 clipboard 차단 시 textarea 자동 선택 fallback

## 의존성

| 사용 | 패키지 | 용도 |
|---|---|---|
| 필수 | `IPython` | `display(HTML(...))` 로 위젯 렌더 |
| 선택 | `pandas` (≥1.3) | `editor.from_dataframes({name: df})` 헬퍼 사용 시 |
| 전이 | (없음) | sqlite3 는 stdlib, 외부 의존 추가 0 |

## 사용 예시

### 기본 — 직접 등록

```python
from sql_editor import SQLEditor

editor = SQLEditor()
editor.add_table("users",  ["id", "name", "email", "created_at"])
editor.add_table("orders", [
    ("id", "INTEGER"),
    ("user_id", "INTEGER"),
    ("amount", "REAL"),
    ("status", "TEXT"),
], description="주문 트랜잭션")
editor.show()   # Jupyter 셀에 렌더
```

### 헬퍼 사용

```python
# (a) dict 한 번에
editor.from_dict({
    "users":  ["id", "name", "email"],
    "orders": ["id", "user_id", "amount"],
})

# (b) SQLite DB 자동 추출 (stdlib)
editor.from_sqlite("./local.db")

# (c) pandas DataFrame 자동 추출
import pandas as pd
df = pd.read_csv("data.csv")
editor.from_dataframes({"data": df})

# 초기 쿼리도 미리 채워두기
editor.set_query("SELECT * FROM users LIMIT 10;")

editor.show()
```

### 워크플로 — 작성한 SQL 을 다른 셀에서 실행

에디터 자체는 쿼리 **실행** 을 하지 않습니다 (의도적 — 실행 인터페이스를 사용자
가 가진 DB/툴에 맡겨 자유도를 높임). 작성한 SQL 을 [SQL 복사] 버튼으로 복사한
뒤 다른 셀에서:

```python
import sqlite3, pandas as pd
conn = sqlite3.connect("./local.db")
sql = """
SELECT u.name, SUM(o.amount) AS total
FROM users u
JOIN orders o ON o.user_id = u.id
WHERE o.status = 'paid'
GROUP BY u.name
ORDER BY total DESC
"""
pd.read_sql(sql, conn)
```

## 스키마 컬럼 형식

`add_table(name, columns)` 의 `columns` 는 다음 셋 중 무엇이든 OK:

```python
# (1) 이름만
["id", "name", "email"]

# (2) (이름, 타입) 또는 (이름, 타입, 설명) 튜플
[("id", "INTEGER"), ("name", "TEXT", "표시 이름")]

# (3) dict
[{"name": "id", "type": "INTEGER", "doc": "PK"}]
```

내부적으로 모두 dict 로 정규화됩니다.

## 파일 구조

```
005-sql-editor-notebook/
├── README.md               # 이 문서
├── sql_editor.py           # single-file 본체 (SQLEditor + HTML/CSS/JS 템플릿)
├── metadata.json
├── LICENSE                 # MIT (오리지널)
└── examples/
    └── basic_usage.py      # 데모 스키마 + HTML 파일로 저장 (브라우저 직접 열기)
```

## 폐쇄망 친화 체크

| 항목 | 상태 |
|---|---|
| 외부 네트워크 호출 / CDN | ❌ 없음 (모든 CSS/JS 인라인) |
| 새 서버 / 포트 | ❌ 없음 |
| 바이너리 영속화 | ❌ 없음 (HTML 파일만 선택적 export) |
| 단일 반입 단위 | `sql_editor.py` 한 파일 |
| 추가 패키지 | IPython 만 (이미 스택 포함) |

## 알려진 제약 / 한계

- **쿼리 실행 기능 없음** — 의도적. 사용자의 DB 환경 (SQLite/PostgreSQL/사내 DW 등) 에 맞춰 별도 셀에서 실행. `SQL 복사` 버튼으로 클립보드 전달이 가장 간편.
- **구문 강조 없음** — textarea 기반이라 단색 다크 테마. 강조가 필요하면 v2 에서 CodeMirror 또는 Monaco 임베드 검토 (현재는 외부 의존을 늘리지 않으려 의도적으로 배제).
- **간이 SQL 파서** — split 기반 토큰화로 문자열 리터럴/주석 일부만 처리. CTE/서브쿼리의 컨텍스트 경계는 정밀하지 않음. 단, 일반 SELECT/JOIN/WHERE 패턴은 잘 동작.
- **노트북 신뢰 정책 (Trust)** — Jupyter 가 셀 출력의 `<script>` 를 차단하면 동작 안 함. 노트북 신뢰가 활성된 환경에서만 작동. 안 되면 `editor.save_html(path)` 로 파일 저장 후 브라우저 직접 열기 가능.
- **다중 셀 독립** — 같은 노트북의 다른 셀에 띄운 에디터는 서로 다른 인스턴스 (각자 고유 `id` 부여).
- **모바일/터치 미고려** — 데스크톱 키보드 + 마우스 전제.
