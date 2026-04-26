# DEMO_STORY.md — 변환물별 시연 시나리오

각 변환물의 **핵심 가치를 한 번에 보여주는 시연 흐름**. 데모 자리에서 따라할 수 있도록 단계별로 정리.

> 셋업: 리포 루트의 통일 `.venv` 가 있어야 함. 자세한 셋업은 [`README.md`](README.md) 의 "개발 환경 셋업" 섹션 참조.
>
> ```bash
> .venv/bin/jupyter lab          # 001 / 005 / 006 노트북용 (이미 떠 있다면 그대로)
> ```

---

## 전체 시연 순서 (제안)

| 단계 | 변환물 | 키 메시지 | 소요 |
|---|---|---|---|
| 1 | [005](#005-sql-editor-notebook--html-단일파일로-쿼리-편집) | "DB 없이 HTML 한 파일만으로 쿼리 편집" | 2분 |
| 2 | [006](#006-sql-codemirror-runner--노트북-안에서-진짜-ide) | "노트북 안에서 진짜 IDE 같은 SQL + 후속 분석" | 4분 |
| 3 | [007](#007-sql-tui-runner--ssh-친화-풀스크린-tui) | "터미널에서도 동일 체감 (ssh 친화)" | 3분 |
| 4 | [002](#002-sentinel-track--폐쇄망-wandb-호환) | "오프라인 학습 트래커 → HTML 대시보드 반출" | 3분 |
| 5 | [001](#001-langgraph-notebook-chatbot--멀티턴-챗봇--트레이서) | "노트북 LangGraph 챗봇 + HITL + 트레이서" | 4분 |
| 6 | [003](#003-langgraph-chat-repl--textual-tui-챗봇) | "터미널 풀스크린 챗봇 (Claude Code 스타일)" | 2분 |
| 7 | [004](#004-langgraph-prompt-toolkit-repl--보수적-환경의-대안) | "textual 못 쓰는 환경의 대안" | 1분 |

총 ~20분.

---

## 005 sql-editor-notebook — "HTML 단일파일로 쿼리 편집"

### 한 줄

> Python 콜백 없이 **HTML 파일 하나만으로** 좌·우 분할 SQL 편집기 + 컨텍스트 자동완성. 폐쇄망에 가장 가벼운 단위.

### 시나리오

1. JupyterLab 좌측 트리에서 `005-sql-editor-notebook/demo.ipynb` 열기
2. 셀 1~3 차례 실행 → 우측 셀 출력에 좌·우 분할 SQL 편집기가 떠야 함
3. 우측 쿼리 입력창에서:
   - `SELECT ` 까지 치고 → **inline popup** 으로 컬럼/`*`/함수 추천이 커서 옆에 뜸 ⭐
   - `FROM ` 다음 → 추천이 자동으로 **테이블** 만 한정
   - `users.` 까지 치면 → users 의 컬럼만 한정 추천 (qualifier)
   - `WHE` 까지만 쳐도 → fallback 으로 `WHERE` 추천
4. 좌측 entity 트리에서 컬럼 클릭 → **커서 위치에 정확히** 인서트되는 것 시연

### 와우 포인트

- "Python 안 써요. 이거 그대로 HTML 로 저장하면 사내망 어디서든 브라우저로 열림"
- 셀에서 `editor.save_html("/tmp/sql.html")` 하면 → 그 HTML 한 파일이 폐쇄망 반출 단위

### 한계 (정직하게)

- 실행 안 됨 (`SELECT * FROM users` 쿼리는 그저 텍스트). 실행하려면 → **다음 데모 (006)** 으로 넘어가는 자연스러운 전환.

---

## 006 sql-codemirror-runner — "노트북 안에서 진짜 IDE"

### 한 줄

> CodeMirror 5 를 single-file `.py` 안에 통째로 인라인. 에디터 자체에 syntax color + popup 자동완성 + `Cmd/Ctrl+Enter` 실행 + 결과 DataFrame 자동 표 렌더 + `runner.last_result` 로 후속 분석.

### 시나리오

1. JupyterLab 에서 `006-sql-codemirror-runner/demo.ipynb` 열기
2. **File → Trust Notebook** ⚠ (인라인 `<script>` 가 차단되면 CM 이 안 뜸)
3. 셀 1, 2 실행 → 풀-패널 SQL Runner 가 셀 출력에 등장
4. 에디터에서 시연 (이 순서로):
   - **(a) 에디터 안에 색이 입혀져 있음** — `SELECT` 핑크, 문자열 노랑, 숫자 주황, 주석 회색 → "노트북 셀 안에서 IDE 체감"
   - **(b) Tab 으로 자동완성 popup 호출** — `name (📝)`, `id (🔢)`, `signup_at (📅)` — **타입을 이모지로 단축** 노출 ⭐
   - **(c) `users.` 입력** → 해당 테이블 컬럼만 한정
   - **(d) 화살표로 커서 이동** → 컨텍스트 추천 칩이 **커서 위치 기준** 으로 실시간 갱신 (← 005/008 대비 차별점)
   - **(e) `Cmd+Enter` (또는 `Ctrl+Enter`) 로 실행** → 셀 하단에 8-열 DataFrame 표가 모든 컬럼 잘림 없이 렌더
5. **다음 셀** 에서:
   ```python
   df = runner.last_result
   df.describe()
   df.to_parquet("/tmp/r.parquet")     # 후속 분석 자유
   runner.history[-1]["query"]
   ```
   → "쿼리 실행 → 결과 객체 보관 → 다음 셀에서 곧장 분석" 흐름 시연 ⭐

### 와우 포인트

- 에디터 안의 진짜 syntax color (006 의 시그니처)
- 타입 이모지 — `id (🔢)` 가 주는 즉각적인 인식
- 결과가 `runner.last_result` / `runner.history` 로 깔끔히 보관
- 폐쇄망 반입 단위는 **`sql_codemirror.py` 한 파일** (~285KB, 외부 자산 0)

### 팁

- "CodeMirror 가 안 떠요" → File → Trust Notebook 부터 확인
- 에디터 영역이 약 30 줄 보이도록 600px 로 잡혀있어서 멀티-라인 쿼리 편함

---

## 007 sql-tui-runner — "ssh 친화 풀스크린 TUI"

### 한 줄

> 006 과 같은 SQL 편집/실행을 **터미널 풀스크린** 에서. JupyterLab / Trust 정책 없이도 ssh 한 번으로.

### 시나리오

1. 터미널 새 창에서:
   ```bash
   .venv/bin/python 007-sql-tui-runner/basic_usage.py
   ```
2. 풀스크린 TUI 진입. 세 영역 (좌 트리 / 우 에디터 / 하단 결과) 가 보여야 함
3. 시연:
   - **(a) 에디터 안 SQL 색** — Textual 의 native tree-sitter SQL grammar 로 키워드/문자열/숫자 색 입혀짐 (006 와 동급 체감) ⭐
   - **(b) 인라인 추천 OptionList** — 에디터 아래에 항상 보이는 추천 리스트가 타이핑마다 갱신
   - **(c) `Tab` 누름** → 포커스가 추천 리스트로 이동, 첫 항목 highlighted
   - **(d) `↑↓ Enter`** → 선택 → 에디터 커서 위치에 인서트 + 자동으로 에디터 복귀
   - **(e) `Ctrl+R`** → 실행 → 하단 DataTable 에 결과 표 (Textual native widget)
   - **(f) `F1`** → 도움말 모달
4. `Ctrl+Q` 종료

### 와우 포인트

- "노트북 안 써도 됩니다. ssh 환경 그대로 실행"
- "Trust 정책에 막힌 환경 (인라인 `<script>` 차단) 에서 006 대안"
- 단일 파일 25KB — 가장 가벼움
- 인라인 OptionList — popup 모달 안 거치고 바로 타이핑/선택 토글

### 팁

- 사내 미러에 `tree-sitter` / `tree-sitter-sql` 미등록 시 → 색만 빠짐, 다른 기능 그대로
- macOS 터미널의 Cmd+Enter 는 안 잡힘 → `Ctrl+R` 또는 `F5` 권장

---

## 002 sentinel-track — "폐쇄망 wandb 호환"

### 한 줄

> `import sentinel_track as wandb` 한 줄로 기존 wandb 코드를 폐쇄망에서 그대로. 학습 메트릭/시스템 메트릭/테이블/이미지 모두 로컬 디렉토리에 저장 → **HTML 대시보드 한 파일** 로 빌드해 반출.

### 시나리오

1. 터미널에서:
   ```bash
   .venv/bin/python 002-sentinel-track/sentinel_track.py demo
   ```
2. 콘솔에 `[sentinel-track] run 시작 ...` 로그 + 자동 생성된 `dashboard.html` 안내 출력
3. **Finder / 브라우저** 에서 `002-sentinel-track/dashboard.html` 열기
4. 보여줄 것:
   - **(a) Run 카드** 리스트 — 실험명, status, 시작/종료 시각, config 요약
   - **(b) 메트릭 차트** — loss/acc 시계열 (인라인 SVG)
   - **(c) GPU/CPU 시스템 메트릭** — psutil 기반 (전력/온도/사용률) ⭐
   - **(d) Table / Image 로깅** — wandb 와 동일 API
5. **개발자 시각**: 기존 wandb 코드를 import 한 줄만 바꾸면 끝
   ```python
   # before:  import wandb
   import sentinel_track as wandb
   ```

### 와우 포인트

- "외부 wandb 서버 호출 0. 학습 메트릭이 사내 디스크에만 쌓이고, HTML 한 파일로 반출"
- `sys.modules['wandb']` 치환 trick 으로 **기존 라이브러리 (transformers Trainer 등) 가 모르고 wandb 인 줄 알고 호출**

### 팁

- torch/transformers 사용 시 `requirements.txt` 에서 의도적으로 빠져있음 → 사용자가 별도 설치 (~5GB). 데모는 `python sentinel_track.py demo` 로 충분 (torch 불필요)

---

## 001 langgraph-notebook-chatbot — "멀티턴 챗봇 + 트레이서"

### 한 줄

> Jupyter 셀 안에서 **LangGraph 멀티턴 챗봇** 을 풀-인터랙션 (HITL 객관식/체크박스 포함) + 모든 흐름을 **LangSmith 스타일 self-contained HTML** 로 export.

### 시나리오

1. JupyterLab 에서 `001-langgraph-notebook-chatbot/demo.ipynb` 열기
2. **0. 인터랙티브 채팅 UI** 셀 실행 → 입력창 + 메시지 영역이 셀 안에 등장
3. 시나리오 입력 (치트시트):
   - **(a) `안녕`** — 일반 대화
   - **(b) `12 + 7 + 100 계산해줘`** — calculator tool 호출 → 트레이스에 tool span 기록 ⭐
   - **(c) `포트폴리오 추천해줘`** — **객관식 HITL** 로 입력창이 라디오 버튼으로 전환 (Enter 또는 숫자키로 선택)
   - **(d) `관심 자산군 여러 개 알려줘`** — **복수선택 HITL** (Space 로 토글, Enter 로 확정)
4. **트레이스 저장 & 링크** 버튼 클릭 → 셀 출력에 trace HTML 링크 등장
5. 새 탭에서 `trace_*.html` 열기:
   - 시간순 노드/엣지 시각화
   - 각 LLM call · tool call 의 input/output 토큰
   - HITL 분기 지점 표시 ⭐

### 와우 포인트

- "노트북 안에서 챗봇 풀-인터랙션 + 트레이스가 별도 파일로 export"
- HITL 변형 (객관식/복수선택) 를 ipywidgets 만으로 구현
- 트레이스 HTML 은 self-contained → 메일 첨부로 동료에게 공유 가능

### 팁

- LLM 은 MockLLM (오프라인 응답) — 실제 LLM 붙이려면 `chatbot.py` 의 `get_llm()` 교체

---

## 003 langgraph-chat-repl — "Textual TUI 챗봇"

### 한 줄

> 001 과 동일 LangGraph 그래프를 **터미널 풀스크린 TUI** 로 (Claude Code 스타일). 인라인 HITL · 슬래시 팔레트 · 트레이스 export 동일.

### 시나리오

1. 터미널에서:
   ```bash
   .venv/bin/python 003-langgraph-chat-repl/basic_usage.py
   ```
2. 풀스크린 진입. 메시지 영역 + 입력창 + 상태 바
3. 시연:
   - **(a) 일반 대화 + tool call 동일 (`12 + 7 + 100 계산해줘`)**
   - **(b) HITL 객관식** — 입력창이 화살표 + 숫자키로 선택 가능한 라디오로 전환 ⭐ (모달 팝업 아님 — Claude Code 처럼 같은 라인에서 변형)
   - **(c) HITL 복수선택** — Space 토글 + Enter
   - **(d) `/` 입력** → 슬래시 팔레트 등장 (`/help`, `/trace`, `/quit` 등)
   - **(e) `Ctrl+O`** → 도구 상세 보기

### 와우 포인트

- "터미널이 익숙한 사용자에겐 노트북 띄우기보다 빠름"
- 001 과 그래프 코드 100% 공유 → 비즈니스 로직은 한 곳

### 팁

- 003 은 textual 6.11.0 사용 → 사내 미러에 `--no-deps` 로만 등록되어 있음 (스택 정책)

---

## 004 langgraph-prompt-toolkit-repl — "보수적 환경의 대안"

### 한 줄

> 003 과 동일 UX 를 **textual 없이 prompt_toolkit 만으로** 재구현. 가장 보수적 폐쇄망 (textual 도 못 받는 환경) 대비.

### 시나리오

1. 터미널에서:
   ```bash
   .venv/bin/python 004-langgraph-prompt-toolkit-repl/basic_usage.py
   ```
2. 003 과 같은 시나리오 (a)~(e) 그대로 재현
3. 보여줄 것:
   - **HITL 인라인 라디오/체크박스** 도 prompt_toolkit Buffer + KeyBindings 만으로 구현 ⭐
   - 모든 상호작용이 003 과 동등

### 와우 포인트

- "textual 미허용 환경에서도 003 의 UX 그대로"
- 의존성: `prompt_toolkit` (이미 ipython 의 transitive — 거의 항상 존재)

### 팁

- 003 보다 약간 덜 화려 (애니메이션 등 일부 미보유) — 의도적 trade-off

---

## 시연 마무리 (1분)

> "변환물은 모두 single-file Python 으로 폐쇄망 반입 단위가 정해져 있고, 같은 컨셉(예: SQL Runner) 도 환경에 따라 4가지 변형(005/006/007/+ deleted) 을 갖춘 것이 차별점입니다. 사내 환경 정책은 [`environment-adapter` Skill](.claude/skills/environment-adapter/) 이 단일 진입점으로 관리합니다."

**Q&A 자주 나오는 질문**

| Q | A |
|---|---|
| 외부 LLM 호출 못 하는데 어떻게 데모? | MockLLM 으로 오프라인 응답. 실제 사내 LLM 붙이는 건 1줄 교체 |
| 사내 미러에 패키지 추가 어떻게 요청? | `requirements.txt` + `environment-adapter` 의 `default.yaml` 두 곳에 같이 갱신 |
| 노트북 trust 가 막힌 환경 | 005 (HTML/JS only) 또는 007 (TUI) 사용 |
| 결과 후속 분석 | 006: `runner.last_result` / `runner.history`. 007 은 DataTable 만 |
| Python 3.8/3.9 환경 | `environment-adapter` 가 문법 다운그레이드 담당 (`X | Y` → `Union[X, Y]` 등) |
