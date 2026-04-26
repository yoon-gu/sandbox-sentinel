# PROMPTING_GUIDE — Claude Code 에 전달한 기능 요구사항 기록

본 문서는 본 저장소에 포함된 6개 single-file 변환물이 Claude Code 와의 협업 과정에서 어떻게 구체화되었는지를 정리한 기록입니다. 사용자(yoon-gu)가 전달한 자연어 요구사항과 그에 따라 반영된 결과를 변환물 단위로 시간 순으로 기술하였습니다.

요구사항은 대부분 한국어 한두 문장으로 전달되었으며, Claude Code 는 환경 정책(`.claude/skills/environment-adapter/`) 및 변환 원칙(`CLAUDE.md`) 을 함께 참조하여 single-file 산출물에 반영하였습니다.

본 가이드는 향후 유사한 변환 작업을 진행할 때 참조할 수 있도록 작성되었습니다.

---

## 1. 변환물별 기능 요구사항 시퀀스

### 001 · langgraph-notebook-chatbot — Jupyter LangGraph 챗봇

| # | 요구사항 (요지) | 반영 결과 |
|---|---|---|
| 1 | Jupyter 노트북 환경에서 동작하는 LangGraph 멀티턴 챗봇 구현 요청 | `chatbot.py` 신규 작성 (Tracer · MockLLM · Graph) |
| 2 | 예제 노트북 전면 개편 요청 | `demo.ipynb` 단계별 셀 재구성 |
| 3 | 첫 번째 셀에서 주요 기능 안내 요청 | 0번 셀에 인터랙티브 채팅 UI 배치 |
| 4 | 단일 선택(Radio)뿐 아니라 다중 선택(Checkbox) UI 도 추가 요청 | HITL `multi_choice` 타입 신설 (Checkbox UI) |
| 5 | "관심 자산군을 여러 개 알려줘" 입력 시 multi-choice 가 노출되지 않는 현상 보고 | MockLLM 트리거 키워드 보강 및 분기 로직 수정 |
| 6 | 실행 트레이스를 LangSmith 스타일로 export 가능하도록 요청 | self-contained `trace_*.html` 출력 기능 신설 |

### 002 · sentinel-track — 오프라인 wandb 호환 실험 트래커

| # | 요구사항 (요지) | 반영 결과 |
|---|---|---|
| 1 | 폐쇄망 환경에서 wandb 와 동등한 동작을 제공하는 single-file 트래커 요청 | `sentinel_track.py` 신규 작성 (`sys.modules['wandb']` 치환 방식) |
| 2 | GPU 메트릭(전력 · 온도) 수집 추가 요청 | `nvidia-smi` 파싱 + `torch.cuda` 메트릭 병합 |
| 3 | 기존 wandb 사용 코드를 수정하지 않은 채 사용 가능하도록 요청 | drop-in 패턴 채택 (`import sentinel_track as wandb`) |
| 4 | 결과를 단일 HTML 파일로 반출 가능하도록 요청 | `dashboard.html` 출력 (인라인 SVG · vanilla JavaScript) |

### 003 · langgraph-chat-repl — Textual 기반 터미널 TUI 챗봇

| # | 요구사항 (요지) | 반영 결과 |
|---|---|---|
| 1 | 001 변환물을 터미널 풀스크린 TUI 로 이식하되 Textual 프레임워크 활용 요청 | `repl.py` 신규 작성 (Tracer · ChatEngine · Textual App) |
| 2 | Ctrl+S 단축키 미동작 보고 | 단축키 재배정 (Ctrl+T · Ctrl+N · Ctrl+O 등) |
| 3 | JupyterLab 터미널 및 SSH 환경에서의 동작 가능 여부 확인 요청 | TTY 환경 호환성 검증 수행 |
| 4 | macOS 환경에 적합한 단축키 체계로 정리 요청 | Mac 표준 키만 사용 (Ctrl · Esc · ↑↓ · Enter · Space · Tab) |
| 5 | popup 대신 입력창에서 화살표로 직접 조작하는 방식 요청 (Claude Code 스타일) | 인라인 HITL 도입 (모달 미사용, 동일 라인 내 변형) |
| 6 | 팔레트 기능을 슬래시(`/`) 명령으로 제공 요청 | 슬래시 팔레트 신설 (`/help` · `/trace` · `/quit` 등) |
| 7 | "tool 더 보기" 단축키를 F3 에서 Ctrl+O 로 변경 요청 | 키 매핑 변경 |
| 8 | 헤더 정렬이 맞지 않으므로 영문을 활용해서라도 정렬 보정 요청 | ASCII 헤더로 정렬 보정 |
| 9 | 숫자 키로 즉시 선택 가능한 UX 요청 | 1-9 즉시 선택 (radio · checkbox) |
| 10 | 즉시 제출이 아닌 1차 선택 후 Enter 확정 방식으로 변경 요청 | 1-9 = 하이라이트, Enter = 확정 |
| 11 | Radio 선택 옵션에도 번호가 노출되도록 요청 | 옵션 라벨 앞에 `1.` · `2.` 자동 prepend |

### 004 · langgraph-prompt-toolkit-repl — Textual 미허용 환경용 대안 구현

| # | 요구사항 (요지) | 반영 결과 |
|---|---|---|
| 1 | Textual 없이 prompt_toolkit 만으로 003 과 동등한 기능을 제공하는 변환물 요청 | `repl.py` 신규 작성 (003 의 UX 를 prompt_toolkit Buffer 기반으로 재구현) |

### 005 · sql-codemirror-runner — CodeMirror 기반 노트북 SQL 편집기

> 본 변환물은 반복 개선이 가장 많이 누적된 산출물입니다.

| # | 요구사항 (요지) | 반영 결과 |
|---|---|---|
| 1 | Jupyter 노트북에서 좌측 entity 트리, 우측 query 입력, 컨텍스트 자동완성을 제공하는 SQL 편집기 요청 | HTML/JS 전용 구조로 초기 작성 |
| 2 | 쿼리 실행 · Enter 단축키 · syntax highlight 추가 요청 | ipywidgets 기반 분기 시도 |
| 3 | syntax highlight 를 별도 영역이 아닌 에디터 자체에 적용 요청 | CodeMirror 인라인 임베드 방식으로 노선 변경 |
| 4 | 실행 결과의 모든 컬럼이 노출되도록 확장 요청 | `pd.option_context(max_columns=None)` 적용 |
| 5 | 추천 칩 패널 추가 요청 | Python 측 `_update_suggest` 신설 |
| 6 | 주요 SQL 키워드가 자동완성에 노출되지 않는 현상 보고 | KEYWORDS / FUNCTIONS fallback 정책 도입 |
| 7 | sqlite 외 다양한 백엔드 연동을 위한 콜백 구조 요청 | `demo.ipynb` 에 4종 백엔드 시연 (raw cursor · mock engine · `df.query` · echo) |
| 8 | Tab 키로 자동완성 트리거하되 편집 중에는 자동 popup 요청 | `inputRead` + setTimeout · `completionActive` 가드 적용 |
| 9 | popup 방식 ↔ 인라인 방식 사이 정책 변경 요청 (반복) | 시행착오 후 최종 popup 방식 유지 |
| 10 | 트리에서 테이블 클릭 시 `SELECT * FROM table` 자동 입력 요청 후 종전 동작으로 환원 요청 | 일시 적용 후 롤백 (이름만 인서트) |
| 11 | 화살표로 커서 이동 시 컨텍스트가 갱신되지 않는 현상 보고 | CodeMirror `cursorActivity` 이벤트로 hidden Textarea 동기화 |
| 12 | 좌측 트리 컬럼 표시 정책 변경 요청 (한 줄 + 타입 + hover doc → 가독성 저하 → 종전 환원) | 원래 방식으로 환원, 추천 표시에만 `(TYPE)` 부기 |
| 13 | 결과창을 셀 전체 너비로 확장 요청 | 레이아웃 재구성 (VBox(HBox(트리, 에디터), 결과 전체너비)) |
| 14 | 다음 셀에서 분석할 수 있도록 runner 객체에 query · result 보관 요청 | `runner.last_query` · `last_result` · `history` 노출 |
| 15 | 추천 표시의 타입을 (TYPE) 형태로 간결화 요청 | 이모지 단축 (`id 🔢` · `signup_at 📅`) |
| 16 | 컨텍스트 첫 줄의 height 가 어긋나는 현상 보고 | inline-flex + box-sizing 적용 |
| 17 | 에디터에서 약 30줄 표시 가능하도록 요청 | `cm.setSize(100%, 600)` 설정 |
| 18 | hover 툴팁에 컬럼명을 제외하고 설명만 표시하도록 요청 | tooltip 단순화 |
| 19 | 타입을 이모지로 단축 표시 요청 | `_short_type()` 도입 (INT → 🔢 · TEXT → 📝 등) |
| 20 | 클립보드 복사가 차단되어 CSV / Excel 다운로드로 대체 요청 | base64 data URI + anchor click 패턴 적용 |
| 21 | 단어 중간부터 타이핑 시 자동완성이 동작하지 않는 현상 보고 | `contextHint` 단어 범위 양방향 확장 + setTimeout 처리 |
| 22 | `SELECT col AS d, col2,` 컨텍스트가 컬럼으로 인식되지 않는 현상 보고 | weak anchor (AS · WITH · VALUES) 콤마 통과 시 skip 정책 도입 |
| 23 | `FROM table AS o · JOIN AS e` 와 같이 alias 정의 시 `o.` · `e.` 자동완성 요청 | `extract_aliases()` 함수 신설 |
| 24 | SELECT 절에서 정의 이전에 alias 를 사용하는 경우 대응 요청 | 커서 전후 전체 텍스트 스캔 도입 |
| 25 | 컬럼 AS 와 alias 가 혼동될 가능성 검토 요청 | 정규식을 FROM · JOIN 시작 위치로 한정, 회귀 테스트로 확인 |
| 26 | 콤마 결합 join 및 schema-qualified 식별자 보강 요청 | `FROM x, y` 및 `FROM public.x AS o` 인식 추가 |
| 27 | CTE 처리 가능 여부 검토 요청 | 구현 보류, README 한계 섹션에 TODO 로 명시 |

### 006 · sql-tui-runner — Textual 기반 터미널 SQL Runner

| # | 요구사항 (요지) | 반영 결과 |
|---|---|---|
| 1 | TUI 형태로 005 와 동등한 기능에 더해 쿼리 실행까지 제공하는 변환물 요청 | `sql_tui.py` 신규 작성 (Textual TextArea + native SQL syntax) |
| 2 | Tab 키로 인라인 OptionList 자동완성 요청 | popup 모달 제거, 항상 노출되는 OptionList 채택 |
| 3 | syntax highlight 미동작 현상 보고 | `tree-sitter-sql` 패키지 추가 + try/except fallback 처리 |
| 4 | 트리에서 테이블 클릭 시 동작 변경 요청 후 종전 환원 요청 | 단순 이름 인서트로 환원 |
| 5 | Tab 을 에디터 indent 로 복구하고 자동완성은 커서 근처 floating popup 으로 변경 요청 | floating popup + `cursor_screen_offset` 활용 |
| 6 | 자동 popup trigger 의 잦은 노출에 대한 개선 요청 | 자동 trigger 제거, 수동 호출 방식으로 변경 |
| 7 | 컨텍스트 라벨 옆에 가능한 항목 목록을 항상 노출 요청 | 항상 표시되는 추천 칩 라인 신설 |
| 8 | Shift+Tab dedent · Ctrl+Enter 실행 단축키 추가 요청 | 키 바인딩 추가 |
| 9 | 에디터 영역 확장 및 결과 영역을 1/3 비율로 축소 요청 | 레이아웃 비율 2fr : 1fr 로 조정 |
| 10 | Ctrl+K 채팅 popup 신설 및 LLM · text2sql 연동 hook 요청 | `_ChatScreen` 및 `on_chat=fn` 콜백 hook 신설 |
| 11 | 노트북 터미널에서 Ctrl+Enter 가 개행으로, Ctrl+Space 가 무동작으로 처리되는 현상 보고 | 키 매핑 재구성 (Ctrl+E 실행 · Ctrl+N popup · F4 Excel · Ctrl+B 에디터 포커스) |
| 12 | 응답을 markdown 으로 정돈된 형태로 표시하고 코드 블록은 syntax highlight 적용 요청 | Rich Markdown 렌더링 + Pygments (monokai) 적용 |
| 13 | 코드 블록 우측 상단에 복사 기능 추가 요청 | `_CodeBlock` widget 클릭 시 OSC 52 escape sequence 로 클립보드 전송, `Ctrl+Y` 키보드 단축키 추가 |
| 14 | 에디터에 Ctrl+/ 라인 주석 토글 기능 추가 요청 | `action_toggle_comment` 신설 (단일/다중 라인 지원, indent 보존) |
| 15 | 채팅 기능이 미완성임을 UI 에 명시적으로 표기 요청 | UI · footer · help · README 전반에 🚧 미완성 마커 추가 |
| 16 | Ctrl+X 를 종료에 매핑하도록 요청 | `Ctrl+Q` 와 함께 `Ctrl+X` 도 종료로 바인딩 |

---

## 2. 관련 문서

- [`README.md`](README.md) — 저장소 전체 개요 및 변환물 인덱스
- [`CLAUDE.md`](CLAUDE.md) — 변환 워크플로 · 코드 스타일 · 원칙 수준의 금지 사항
- [`DEMO_STORY.md`](DEMO_STORY.md) — 변환물별 시연 시나리오 (약 17분 분량)
- 각 변환물 폴더의 `README.md` — 구현 범위 · 사용 방법 · 의도적으로 제외한 기능 명세
