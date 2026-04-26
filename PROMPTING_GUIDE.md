# PROMPTING_GUIDE.md — 어떤 기능 요구사항을 Claude 에게 부탁했나

> 이 리포의 6개 변환물은 사용자(yoon-gu)가 Claude Code 에게 던진 일련의 **기능 요구사항**으로 만들어졌습니다. 어떤 요청이 어떤 기능으로 이어졌는지 변환물별로 정리합니다.

요청은 거의 모두 한국어 한두 줄. Claude 가 환경 정책(`.claude/skills/environment-adapter/`) + 변환 원칙(`CLAUDE.md`) 을 읽고 single-file 변환물에 반영합니다.

---

## 변환물별 기능 요구사항 시퀀스

### 001 — langgraph-notebook-chatbot (Jupyter LangGraph 챗봇)

| # | 요구사항 (요지) | 결과 |
|---|---|---|
| 1 | "Jupyter 노트북에서 돌아가는 LangGraph 멀티턴 챗봇 만들어주세요" | `chatbot.py` 신규 (Tracer + MockLLM + Graph) |
| 2 | "예제 노트북 전면 개편해주세요!" | `demo.ipynb` 단계별 셀 재구성 |
| 3 | "이런 기능들을 첫번째 셀에서 설명" | 0번 셀 = 인터랙티브 채팅 UI |
| 4 | "Radio 버튼도 있지만 여러 개 고를 수 있는 체크박스도 추가해주세요" | HITL `multi_choice` 타입 추가 (Checkbox UI) |
| 5 | "관심 자산군 여러 개 알려줘 라고 했는데 multi choice 가 안 나옴" | MockLLM 트리거 키워드 보강 + 분기 수정 |
| 6 | "트레이스를 LangSmith 스타일로 export 가능하게" | self-contained `trace_*.html` 신규 |

### 002 — sentinel-track (오프라인 wandb 호환 트래커)

| # | 요구사항 (요지) | 결과 |
|---|---|---|
| 1 | "폐쇄망에서 wandb 못 쓰는데 비슷하게 동작하는 single-file 트래커" | `sentinel_track.py` 신규 (`sys.modules['wandb']` 치환) |
| 2 | "GPU 메트릭 (전력/온도) 도 포함" | `nvidia-smi` 파싱 + `torch.cuda` 머지 |
| 3 | "기존 wandb 코드 안 바꾸고 그대로" | drop-in 패턴 (`import sentinel_track as wandb`) |
| 4 | "결과를 HTML 파일 한 장으로 반출" | `dashboard.html` (인라인 SVG + vanilla JS) |

### 003 — langgraph-chat-repl (Textual TUI 챗봇)

| # | 요구사항 (요지) | 결과 |
|---|---|---|
| 1 | "001 을 터미널 풀스크린 TUI 로 만들어주세요. Textual 반영" | `repl.py` 신규 (Tracer + ChatEngine + Textual App) |
| 2 | "Ctrl+S 가 동작 안 함" | 단축키 재배정 (Ctrl+T, Ctrl+N, Ctrl+O 등) |
| 3 | "이게 jupyter lab 터미널 / SSH 터미널에서 됨?" | TTY 환경 호환성 검증 |
| 4 | "shortcut 들을 macOS 친화로" | Mac 표준 키만 (Cmd 대신 Ctrl, Esc, ↑↓, Enter, Space, Tab) |
| 5 | "popup 대신 입력창에서 직접 화살표로 조작 (Claude Code 처럼)" | 인라인 HITL (모달 X, 같은 라인에서 변형) |
| 6 | "팔레트 기능을 슬래시(`/`) 로" | 슬래시 팔레트 (`/help`, `/trace`, `/quit` 등) |
| 7 | "tool 더 보기는 F3 말고 Ctrl+O 로" | 키 변경 |
| 8 | "맨위 헤더 모양이 안 맞으니 영어를 써서라도 맞춰주세요" | ASCII 헤더로 정렬 보정 |
| 9 | "선택할때 숫자키로 즉시 즉시 선택 가능" | 1-9 즉시 선택 (radio/checkbox) |
| 10 | "즉시 제출 말고 한번 누르고 엔터" | 1-9 = 하이라이트만, Enter = 확정 |
| 11 | "Radio 버튼으로 선택하는 것들도 번호가 나왔으면" | 옵션 라벨 앞에 `1.`, `2.` 자동 prepend |

### 004 — langgraph-prompt-toolkit-repl (textual 없는 환경의 대안)

| # | 요구사항 (요지) | 결과 |
|---|---|---|
| 1 | "Textual 없이 prompt_toolkit 만 사용해서 같은 기능 만들어주세요. 004 로" | `repl.py` 신규 (003 동일 UX 를 prompt_toolkit Buffer 로 재구현) |

### 005 — sql-codemirror-runner (CodeMirror 노트북, 가장 많이 다듬음)

| # | 요구사항 (요지) | 결과 |
|---|---|---|
| 1 | "Jupyter 노트북에서 SQL 편집기. 좌 entity 트리, 우 query 입력, 컨텍스트 자동완성" | HTML/JS only 로 시작 |
| 2 | "쿼리 실행 + Enter 동작 + syntax highlight 도 필요" | ipywidgets 분기 시도 |
| 3 | "syntax 하이라이트를 따로 보여주지 말고 editor 에 적용" | CodeMirror 인라인 임베드로 노선 변경 |
| 4 | "실행결과를 모든 컬럼까지 확장" | `pd.option_context(max_columns=None)` 적용 |
| 5 | "추천 칩 패널도 추가" | Python 사이드 `_update_suggest` 신설 |
| 6 | "주요 쿼리 문법이 자동완성에 안 뜸" | KEYWORDS/FUNCTIONS fallback 정책 |
| 7 | "sqlite 외 백엔드 콜백 구조" | demo.ipynb 에 4가지 백엔드 시연 (raw cursor, mock engine, df.query, echo) |
| 8 | "Tab 으로 자동완성 트리거. 편집 중엔 자동 popup" | `inputRead` + setTimeout / completionActive 가드 |
| 9 | "popup 말고 인라인으로" → 다시 "popup 도 OK" | popup ↔ 인라인 사이 시행착오 → 최종 popup 유지 |
| 10 | "트리에서 table 클릭 → SELECT * FROM table" → 다시 "예전처럼" | 일시 적용 후 롤백 (이름만 인서트) |
| 11 | "화살표로 커서 옮기면 컨텍스트가 갱신 안 됨" | CM `cursorActivity` → 별도 hidden Textarea 동기화 |
| 12 | "왼쪽 트리 컬럼 한 줄씩 + 타입 + 호버 doc tooltip" → "글씨 너무 작아 겹침" → "예전처럼" | 결국 원복 + 추천 표시에만 `(TYPE)` 표시 |
| 13 | "결과창 셀 전체 너비로" | layout 재구성: VBox(HBox(트리,에디터), 결과 전체너비) |
| 14 | "runner 객체에 query/result 보관 (다음 셀 분석용)" | `runner.last_query/last_result/history` |
| 15 | "엔트리 글씨 크게 필요 없고 추천 표시만 (TYPE) 으로" | 이모지 단축 (`id 🔢` / `signup_at 📅`) |
| 16 | "컨텍스트 첫줄 height 가 안 맞음" | inline-flex + box-sizing |
| 17 | "에디터 약 30줄 보이게" | `cm.setSize(100%, 600)` |
| 18 | "hover 시 설명만 (컬럼명 X)" | tooltip 단순화 |
| 19 | "타입 이모지로 단축" | `_short_type()` (INT→🔢, TEXT→📝, …) |
| 20 | "SQL 복사 (clipboard) 차단됨. CSV/Excel 다운로드로" | base64 data URI + anchor.click 패턴 |
| 21 | "중간부터 타이핑 시 자동완성 안 됨" | contextHint word 범위 양쪽 확장 + setTimeout |
| 22 | "`SELECT col AS d, col2,` 다음 컨텍스트가 컬럼이 아님" | weak anchor (AS/WITH/VALUES) 콤마 통과 후 skip |
| 23 | "FROM table AS o, JOIN AS e — `o.` `e.` 자동완성" | `extract_aliases()` 신설 |
| 24 | "다시 SELECT 절에서 alias 쓰는 경우" | full text 스캔 (cursor 앞뒤 모두) |
| 25 | "컬럼 AS 와 안 헷갈리지?" | 정규식이 FROM/JOIN 시작 한정 — 회귀 테스트로 확인 |
| 26 | "한계 예시 + 콤마 join / schema-qualified 보강" | `FROM x, y` + `FROM public.x AS o` 인식 추가 |
| 27 | "CTE 도 가능하지 않을까?" → "TODO 로" | README 한계 섹션에 기록만 |

### 006 — sql-tui-runner (Textual TUI SQL Runner)

| # | 요구사항 (요지) | 결과 |
|---|---|---|
| 1 | "이런 게 그냥 TUI 로 되는 게 더 깔끔할까요? 005 와 같은 기능에 실행까지" | `sql_tui.py` 신규 (Textual TextArea + native SQL syntax) |
| 2 | "Tab 으로 인라인 OptionList 자동완성" | popup 모달 제거 + 항상 보이는 OptionList |
| 3 | "syntax highlight 안되네요?" | tree-sitter-sql 패키지 추가 + try/except fallback |
| 4 | "Tree 에서 table 클릭 → SELECT * FROM table" → "예전처럼" | 단순 인서트로 복구 |
| 5 | "Tab 을 editor indent 로 복구. 자동완성은 cursor 근처 floating popup" | floating popup + `cursor_screen_offset` |
| 6 | "매번 자동 trigger 너무 힘드네. 수동 호출로" | 자동 trigger 제거 |
| 7 | "ctx-label 옆에 가능한 항목 줄줄이 표시" | 항상 보이는 추천 칩 라인 |
| 8 | "Shift+Tab dedent · Ctrl+Enter 실행" | 키 바인딩 추가 |
| 9 | "Editor height 더 길게 + 결과 1/3" | layout 비율 2fr : 1fr |
| 10 | "Ctrl+K 로 채팅 popup. 나중에 LLM/text2sql 연동하고 싶음" | `_ChatScreen` + `on_chat=fn` hook |
| 11 | "노트북 터미널에서 Ctrl+Enter 가 줄바꿈, Ctrl+Space 안 됨" | 키매핑 재구성 (Ctrl+E 실행 / Ctrl+N popup / F4 Excel / Ctrl+B 에디터) |
| 12 | "응답이 마크다운인데 이쁘게 + 코드블록 syntax highlight" | Rich Markdown + Pygments (monokai) |
| 13 | "코드블록 우측상단 복사 기능" | `_CodeBlock` widget 클릭 → OSC 52 클립보드. `Ctrl+Y` 키보드 단축 |
| 14 | "에디터에 Ctrl+/ 주석 토글" | `action_toggle_comment` (단일/다중라인, indent 보존) |
| 15 | "챗봇은 미완성 표시" | UI/footer/help/README 에 🚧 미완성 표기 |
| 16 | "Ctrl+X 는 종료로" | `Ctrl+Q` / `Ctrl+X` 둘 다 종료 |

---

## 참고

- [`README.md`](README.md) — 리포 전체 개요 + 변환물 인덱스
- [`CLAUDE.md`](CLAUDE.md) — 변환 워크플로 / 코드 스타일 / 원칙 레벨 금지사항
- [`DEMO_STORY.md`](DEMO_STORY.md) — 변환물별 시연 시나리오 (~17분)
- 각 변환물의 `README.md` — 무엇을 / 어떻게 / 무엇을 의도적으로 뺐는지
