# 004 - LangGraph Chat REPL (prompt_toolkit 판)

> **한 줄 요약**: 003 과 동일한 Claude Code 풍 TUI 챗봇을 **prompt_toolkit 만** 으로 구현한 single-file REPL. Textual 이 사내 미러에 없어도 돌아감 (prompt_toolkit 은 ipython 의 필수 전이 의존).

## 003 과의 차이

| 항목 | 003 (textual) | 004 (prompt_toolkit) |
|---|---|---|
| TUI 프레임워크 | `textual` — 스택에 잠정 추가 | `prompt_toolkit` — ipython 전이 의존, 거의 항상 설치됨 |
| 레이아웃 엔진 | Textual CSS | prompt_toolkit `HSplit` / `ConditionalContainer` |
| HITL choice/multi | `OptionList` / 커스텀 `Widget` | `FormattedTextControl` + focusable `KeyBindings` |
| Tool 상세 | 전용 `ModalScreen` 으로 팝업 | 히스토리에 펼쳐서 직접 작성 (일관된 UX) |
| 사내 미러 적합성 | textual 등록 여부 확인 필요 | prompt_toolkit 은 ipython 있으면 함께 옴 |

**UX 는 동일** — 인라인 HITL (팝업 없음), 슬래시 팔레트, Tab 자동완성, trace 저장, macOS 친화 키(Enter / Esc / ↑↓ / Space / Tab / **1-9 즉시 선택·토글**).

## 원본 출처

| 항목 | 값 |
|---|---|
| 라이브러리 | [langgraph](https://github.com/langchain-ai/langgraph) + [prompt_toolkit](https://github.com/prompt-toolkit/python-prompt-toolkit) |
| 버전 | langgraph `1.0.10` · prompt_toolkit `3.0+` |
| 라이선스 | MIT (langgraph) · BSD-3-Clause (prompt_toolkit) |

## 기능 요약

- **`launch(graph, llm)` 한 줄 기동** — 사용자가 자신의 LangGraph 그래프 + LLM 어댑터를 넘기면 풀스크린 REPL 가동
- **레이아웃** (위→아래):
  - 히스토리 `TextArea` (read-only · 스크롤 가능 · wrap)
  - 상태바 (thread · LLM 호출수 · 토큰 · latency · 처리중/HITL 대기 표시)
  - HITL 배너 (조건부, HITL 모드 시 노출 — 🤚 객관식/복수선택/주관식 + 질문 + 키 힌트)
  - 슬래시 힌트 (조건부, `/` 로 시작 시 노출)
  - 입력 영역 (조건부로 스왑):
    - 일반 대화 → `TextArea` (`> `)
    - 주관식 HITL → `TextArea` (`답변> ` placeholder)
    - 객관식 HITL → 커스텀 `FormattedTextControl` (화살표 + Enter)
    - 복수선택 HITL → 커스텀 `FormattedTextControl` (화살표 + Space 토글 + Enter)
- **인라인 HITL** — 입력창이 그 자리에서 HITL 위젯으로 전환, 모달 팝업 없음
- **슬래시 팔레트**:
  - `/` 타이핑 시 명령 목록이 인라인 힌트로 노출 (실시간 필터링)
  - 첫 매치는 `▸` 마커로 강조
  - `Tab` 으로 첫 매치 자동완성 (filter 조건부 바인딩 — 일반 모드 + `/` 시작일 때만)
- **슬래시 명령** (macOS 친화 표준 키만):
  - `/new` — 새 thread · `Ctrl+N`
  - `/trace` — HTML 저장 · `Ctrl+T`
  - `/history` — 대화 이력 다시 출력
  - `/tool` (또는 `/tools`, `/details`) — tool 상세 `Ctrl+O`
  - `/help` — 도움말 · `F1`
  - `/quit` — 종료 · `Ctrl+C`
- **Tool 상세** (`Ctrl+O` 또는 `/tool`) — Tracer 의 `tool:*` span 의 `inputs` / `outputs` / `metadata` / `error` 를 **히스토리에 직접 펼침** (스크롤 가능). 모달 사용 안 함 — UX 일관성
- **트레이스** — `/trace` 또는 `Ctrl+T` → `trace_<thread>_<ts>.html` 저장. 브라우저로 열면 JS 기반 span 트리 확인 (001 와 동일 포맷)
- **그래프 실행은 `asyncio.run_in_executor` 로 백그라운드** — UI 프리즈 방지, 상태바가 `⏳ 처리중` 표시
- **외부 네트워크 / 새 서버 / 포트 오픈 0** — 터미널 TTY 만 사용

## 의존성

| 사용 | 패키지 | 용도 |
|---|---|---|
| 필수 | `langgraph` | StateGraph · MemorySaver · interrupt · Command |
| 필수 | `prompt_toolkit` | 풀스크린 TUI (ipython 전이 의존이라 거의 항상 설치됨) |
| (없음) | — | stdlib 외 추가 의존 없음 |

> **스택 적합성**: 사내 미러에 `ipython` 이 있으면 `prompt_toolkit` 도 함께 깔립니다. `stacks/default.yaml` 에도 명시되어 있음. textual 등록이 안 돼 있어도 이 변환물은 동작.

## 사용 예시

### 빠른 체험 (MockLLM + HITL 포함 그래프)

```bash
# 리포 루트의 통일 .venv 를 사용 (셋업: 루트 README 참고)
.venv/bin/python 004-langgraph-prompt-toolkit-repl/examples/basic_usage.py
```

실행 후 입력창에 치트시트 문구를 넣어보세요:

- `안녕` — 일반 대화
- `12 + 7 + 100 계산해줘` — tool span (calculator) 가 트레이스에 기록됨
- `포트폴리오 추천해줘` — 객관식 HITL 로 입력창 전환 (**`1-9`** 또는 `↑↓` 로 이동 후 `Enter` 로 확정)
- `관심 자산군 여러 개 알려줘` — 복수선택 HITL 로 전환 (Space · Enter)
- `상황을 구체적으로 설명해줘` — 주관식 HITL 로 전환 (타이핑 · Enter)
- `/` — 슬래시 팔레트 힌트 노출
- `/tr` + `Tab` — `/trace` 로 자동완성
- `/tool` 또는 `Ctrl+O` — 지금까지의 tool 호출 상세를 히스토리에 펼침
- `/new` 또는 `Ctrl+N` — 새 thread 로 리셋
- `/quit` 또는 `Ctrl+C` — 종료

### 자신의 그래프로 REPL 띄우기

```python
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from repl import launch

g = StateGraph(MyState)
g.add_node("chat", my_chat_node)
g.add_edge(START, "chat")
g.add_edge("chat", END)
compiled = g.compile(checkpointer=MemorySaver())

from my_company.llm import InHouseLLM
launch(graph=compiled, llm=InHouseLLM())
```

LLM 어댑터 인터페이스는 003 과 동일: `invoke(messages: list[dict]) -> dict` 만 구현.

## 파일 구조

```
004-langgraph-prompt-toolkit-repl/
├── README.md               # 이 문서
├── repl.py                 # single-file 본체 (Tracer + ChatEngine + ReplApp + launch)
├── metadata.json
├── LICENSE                 # langgraph MIT
└── examples/
    └── basic_usage.py      # MockLLM + HITL 트리거 그래프 → REPL 기동 (003 과 동일)
```

## 폐쇄망 친화 체크

| 항목 | 상태 |
|---|---|
| 외부 네트워크 호출 | ❌ 없음 (터미널 TTY 만 사용) |
| 새 서버 프로세스 / 포트 오픈 | ❌ 없음 |
| 바이너리 영속화 | ❌ 없음 (트레이스만 HTML) |
| 외부 CDN | ❌ 없음 |
| 사내 반입 단위 | single `repl.py` |
| 사내 미러 의존성 | `langgraph` + `prompt_toolkit` (ipython 전이) |

## 알려진 제약 / 한계

- **MockLLM 은 시뮬레이터** — 실제 응답 품질 필요 시 사내 LLM 어댑터 연결
- **단일 라인 입력** (v1). 멀티라인은 v2 예정
- **async 그래프 (`ainvoke`) 미지원** — 동기 `invoke()` 기준
- **히스토리 스크롤**: TextArea 의 기본 동작 의존. 매우 긴 이력에선 성능 확인 필요
- **ANSI 256색 · 유니코드 박스 문자** 지원 터미널 전제

## 003 vs 004 선택 기준

| 상황 | 추천 |
|---|---|
| **사내 미러에 `textual==6.11.0` 등록됨** (2026-04-24 확인, `--no-deps` 로 설치) | **003** (textual) — 좀 더 리치한 스타일, 사내 표준 있음 |
| 타 조직 / 다른 폐쇄망에 textual 없음 | **004** (prompt_toolkit) — ipython 있으면 거의 확실히 동작 |
| 사내에 ipython 도 없다 (매우 드뭄) | 둘 다 안 됨 — 더 기초적인 stdlib-only REPL 을 새로 만들어야 함 |

기능적으로는 UX 차이가 거의 없으니 **스택 적합성** 기준으로 고르면 됩니다. **현재 기본 스택(`stacks/default.yaml`)은 textual 6.11.0 을 명시**하므로 003 이 1차 선택이고, 004 는 textual 제약이 있는 타 환경용 백업입니다.
