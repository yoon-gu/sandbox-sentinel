# PROMPTING_GUIDE.md — 어떤 요구사항을 Claude 에게 부탁했나

> 이 리포의 6개 변환물 + 리포 전반은 사용자(yoon-gu)가 Claude Code 에게 일련의 **요구사항**을 던지면서 만들어졌습니다. 어떤 요청이 어떤 변환물·기능으로 이어졌는지 시간 순으로 기록합니다. 같은 흐름으로 본인 도구를 만들고 싶을 때 참고용.

요청은 거의 모두 한국어 한두 줄. Claude 가 환경 정책(`.claude/skills/environment-adapter/`) + 변환 원칙(`CLAUDE.md`) 을 읽고 그에 맞춰 single-file 변환물로 만듭니다.

---

## 변환물별 요구사항 시퀀스

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
| 3 | "psutil 사내 미러에 추가됐어요" | `default.yaml` 의 `allowed_packages` 갱신 |
| 4 | "기존 wandb 코드 안 바꾸고 그대로" | drop-in 패턴 (`import sentinel_track as wandb`) |
| 5 | "결과를 HTML 파일 한 장으로 반출" | `dashboard.html` (인라인 SVG + vanilla JS) |

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
| 12 | "textual 6.11.0 을 `--no-deps` 로 깔 수 있음" | `default.yaml` 의 textual 버전 핀 + 주석 |

### 004 — langgraph-prompt-toolkit-repl (textual 없는 환경의 대안)

| # | 요구사항 (요지) | 결과 |
|---|---|---|
| 1 | "Textual 없이 prompt_toolkit 만 사용해서 같은 기능 만들어주세요. 004 로" | `repl.py` 신규 (003 동일 UX 를 prompt_toolkit Buffer 로 재구현) |
| 2 | "다 3.11 로 해주셔야죠" | venv 재생성 + 검증 절차를 SKILL.md 에 추가 |
| 3 | "이 내용은 클로드가 매번 체크할 수 있게 추가" | environment-adapter SKILL.md 의 "런타임 환경 검증" 섹션 |

### 005 — sql-codemirror-runner (CodeMirror 노트북, 가장 많이 다듬음)

| # | 요구사항 (요지) | 결과 |
|---|---|---|
| 1 | "Jupyter 노트북에서 SQL 편집기. 좌 entity 트리, 우 query 입력, 컨텍스트 자동완성" | (이전 005-sql-editor-notebook — 후일 삭제) HTML/JS only 시작 |
| 2 | "쿼리 실행 + Enter 동작 + syntax highlight 도 필요" | (구) 006 ipywidgets 분기 시도 (별도 변환물로) |
| 3 | "syntax 하이라이트를 따로 보여주지 말고 editor 에 적용" | CodeMirror 인라인 임베드로 노선 변경 → 현재의 005 |
| 4 | "백업용 커밋하고, CodeMirror 인라인 안으로 진행" | 별도 변환물 신규 (~290KB single-file) |
| 5 | "실행결과를 모든 컬럼까지 확장" | `pd.option_context(max_columns=None)` 적용 |
| 6 | "추천 칩 패널도 추가" | Python 사이드 `_update_suggest` 신설 |
| 7 | "주요 쿼리 문법이 자동완성에 안 뜸" | KEYWORDS/FUNCTIONS fallback 정책 |
| 8 | "sqlite 외 백엔드 콜백 구조" | demo.ipynb 에 4가지 백엔드 시연 (raw cursor, mock engine, df.query, echo) |
| 9 | "Tab 으로 자동완성 트리거. 편집 중엔 자동 popup" | `inputRead` + setTimeout / completionActive 가드 |
| 10 | "popup 말고 인라인으로" → 다시 "popup 도 OK" | popup ↔ 인라인 사이 시행착오 → 최종 popup 유지 |
| 11 | "트리에서 table 클릭 → SELECT * FROM table" → 다시 "예전처럼" | 일시 적용 후 롤백 (이름만 인서트) |
| 12 | "화살표로 커서 옮기면 컨텍스트가 갱신 안 됨" | CM `cursorActivity` → 별도 hidden Textarea 동기화 |
| 13 | "왼쪽 트리 컬럼 한 줄씩 + 타입 + 호버 doc tooltip" → "글씨 너무 작아 겹침" → "예전처럼" | 결국 원복 + 추천 표시에만 `(TYPE)` 표시 |
| 14 | "결과창 셀 전체 너비로" | layout 재구성: VBox(HBox(트리,에디터), 결과 전체너비) |
| 15 | "runner 객체에 query/result 보관 (다음 셀 분석용)" | `runner.last_query/last_result/history` |
| 16 | "엔트리 글씨 크게 필요 없고 추천 표시만 (TYPE) 으로" | 이모지 단축 (`id 🔢` / `signup_at 📅`) |
| 17 | "컨텍스트 첫줄 height 가 안 맞음" | inline-flex + box-sizing |
| 18 | "에디터 약 30줄 보이게" | `cm.setSize(100%, 600)` |
| 19 | "hover 시 설명만 (컬럼명 X)" | tooltip 단순화 |
| 20 | "타입 이모지로 단축" | `_short_type()` (INT→🔢, TEXT→📝, …) |
| 21 | "SQL 복사 (clipboard) 차단됨. CSV/Excel 다운로드로" | base64 data URI + anchor.click 패턴 |
| 22 | "중간부터 타이핑 시 자동완성 안 됨" | contextHint word 범위 양쪽 확장 + setTimeout |
| 23 | "`SELECT col AS d, col2,` 다음 컨텍스트가 컬럼이 아님" | weak anchor (AS/WITH/VALUES) 콤마 통과 후 skip |
| 24 | "FROM table AS o, JOIN AS e — `o.` `e.` 자동완성" | `extract_aliases()` 신설 |
| 25 | "다시 SELECT 절에서 alias 쓰는 경우" | full text 스캔 (cursor 앞뒤 모두) |
| 26 | "컬럼 AS 와 안 헷갈리지?" | 정규식이 FROM/JOIN 시작 한정 — 회귀 테스트로 확인 |
| 27 | "한계 예시 + 콤마 join / schema-qualified 보강" | `FROM x, y` + `FROM public.x AS o` 인식 추가 |
| 28 | "CTE 도 가능하지 않을까?" → "TODO 로" | README 한계 섹션에 기록만 |
| 29 | "시연 영상 깃헙에 보이게" | `screencast.mp4` 첨부 → 깨짐 발견 → `screencast.gif` 로 교체 |

### 006 — sql-tui-runner (Textual TUI SQL Runner)

| # | 요구사항 (요지) | 결과 |
|---|---|---|
| 1 | "이런 게 그냥 TUI 로 되는 게 더 깔끔할까요? 005 와 같은 기능에 실행까지" | `sql_tui.py` 신규 (Textual TextArea + native SQL syntax) |
| 2 | "tree-sitter 가 javascript 라서 html 에 영속화하면 됨?" | TUI 환경 한계 설명 + 그대로 유지 |
| 3 | "tree-sitter 의존성 없앨 수 있어?" | "에디터 색만 빠짐, 다른 기능 동일" → "그대로 두시지요" |
| 4 | "Tab 으로 인라인 OptionList 자동완성" | popup 모달 제거 + 항상 보이는 OptionList |
| 5 | "syntax highlight 안되네요?" | 원인 (tree-sitter-sql 패키지 누락) → 설치 + try/except fallback |
| 6 | "Tree 에서 table 클릭 → SELECT * FROM table" → "예전처럼" | 005 와 비슷한 시행착오 후 단순 인서트로 복구 |

---

## 리포 전반 (cross-cutting) 요구사항

| 단계 | 요구사항 | 결과 |
|---|---|---|
| 환경 | "001~007 venv 환경 통일할 수 있나요?" | 7개 .venv (1.27GB) → 루트 단일 .venv (359MB, -71%) |
| 환경 | "torch 의존도까지는 필요 없을 것 같습니다" | requirements.txt 에서 torch/transformers 의도적 제외 |
| 환경 | "tree-sitter 등록되어 있다고 가정" | requirements.txt 에 추가, 사내 미러 가정 |
| 정리 | "노트북 변환물 examples 안의 .py 는 불필요" | 005/006/007 의 basic_usage.py 삭제 |
| 정리 | "전반적으로 모든 폴더들 examples 폴더 없애주세요" | 모든 변환물의 `examples/` 디렉토리 평탄화 — 파일을 변환물 루트로 |
| 정리 | "006 (예전 sql_runner_notebook) 은 필요없으니 삭제 + 재번호" | 변환물 7개 → 6개 → … 후속 |
| 정리 | "005 (sql-editor-notebook HTML/JS only) 도 필요 없음. 재번호" | 최종 6개로 정리 (현 005/006 = 구 006/007) |
| 정리 | "browser-demo-automation skill 도 성능 별로니 삭제" | `.claude/skills/browser-demo-automation/` 제거 |
| 정리 | "런타임 산출물 (artifacts/, demo.gif, trace.html, scheduled_tasks.lock) 정리" | 삭제 + .gitignore 강화 |
| 정리 | "examples 폴더 / *-Copy*.ipynb / sentinel_runs / dashboard.html .gitignore 추가" | gitignore 패턴 누적 보강 |
| 노트북 운영 | "노트북 서버 띄워주세요" / "주소 알려주세요" / "관련 jupyter lab 들 모두 종료" | JupyterLab 백그라운드 시작·중지 |
| 문서 | "각각 가장 기능들을 잘 보여주는 데모 시나리오를 DEMO_STORY.md 에" | `DEMO_STORY.md` 신규 (변환물별 시연 단계 + 와우 포인트 + 팁) |
| 문서 | "어떻게 클로드에게 물어보면 이런 앱을 만들어주는지" | (이 문서) `PROMPTING_GUIDE.md` |
| Git | "백업용으로 커밋" / "푸시해주세요" / "회사명은 기록에서 전부 없애주세요" | 단계마다 git commit/push, 민감 정보 cleanup |
| 환경 | "Sequence Ai 라는 건 기록에서 전부 없애주세요" | 강제 푸시로 회사명 제거 |
| 메타 | "asset 들이랑 _build, _template 은 무슨 역할?" / "외부자산 단일 파일로?" | 빌드 단계 / 인라인 패턴 / 이미 그렇게 되어있음 설명 |

---

## 패턴 분류 (이번 리포 기준)

이 리포에서 실제 등장한 요청 유형을 나눠보면:

| 카테고리 | 비중 (체감) | 예시 |
|---|---|---|
| **신규 변환물 생성** | ~10% | "Jupyter 챗봇 만들어주세요", "Textual TUI 로", "CodeMirror 인라인" |
| **UX 미세 조정** | ~40% | "글씨 크기", "색", "여백", "단축키", "라이브러리 X 호환" |
| **버그 신고** | ~15% | "이 쿼리에서 컨텍스트 안 잡힘", "syntax highlight 안 됨" |
| **기능 추가** | ~15% | "alias 인식", "다음 셀 분석용 history", "Excel 다운로드" |
| **롤백 / 단순화** | ~10% | "예전처럼", "그대로 두시지요", "TODO 로 보냅시다" |
| **구조 정리** | ~10% | "examples 없애주세요", "재번호", "venv 통일", "삭제" |

→ **"신규 0% + UX/버그/기능/롤백 80% + 정리 20%"** 가 평균 분포. 큰 신규는 적고 작은 다듬기가 절대 다수.

---

## 시간 순 흐름 한눈에 보기

```
 001 챗봇 ─→ 002 트래커 ─→ 003 textual TUI ─→ 004 prompt_toolkit
                                                       │
                                                       ▼
       (구) 005 SQL editor (HTML/JS) ─→ (구) 006 SQL runner (ipywidgets)
                                                       │
                                                       ▼ (사용자: 더 IDE 같은 체감 원해)
                                            007 SQL CodeMirror 인라인
                                                       │
                                                       ▼ (사용자: TUI 도 만들어봅시다)
                                                  008 SQL Textual TUI
                                                       │
                                                       ▼ (정리)
                  ─→ 005/006 ipywidgets 삭제 (어중간) → 005=CM, 006=TUI 로 재번호
                  ─→ examples/ 폴더 평탄화
                  ─→ venv 통일 (7개 → 1개)
                  ─→ 005 (HTML/JS only) 삭제 → 005=CM, 006=TUI 로 최종
                  ─→ DEMO_STORY.md, PROMPTING_GUIDE.md (이 문서)
```

---

## 참고

- [`README.md`](README.md) — 리포 전체 개요 + 변환물 인덱스 + 환경 셋업
- [`CLAUDE.md`](CLAUDE.md) — 변환 워크플로 / 코드 스타일 / 원칙 레벨 금지사항
- [`DEMO_STORY.md`](DEMO_STORY.md) — 변환물별 시연 시나리오 (~17분)
- [`.claude/skills/environment-adapter/SKILL.md`](.claude/skills/environment-adapter/SKILL.md) — 환경 정책 진입점
- 각 변환물의 `README.md` — 무엇을 / 어떻게 / 무엇을 의도적으로 뺐는지

이 문서 자체도 **"PROMPTING_GUIDE.md 다시 써주세요. 어떤 요건사항을 부탁했는지 관점으로"** 라는 한 줄 요청에서 시작했습니다 — 이 리포의 다른 모든 변환물처럼.
