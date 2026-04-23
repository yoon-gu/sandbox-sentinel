# 003 - LangGraph Chat REPL

> **한 줄 요약**: 사용자가 정의한 LangGraph 그래프를 **터미널 풀스크린 TUI** (Claude Code 스타일 — 상단 히스토리 · 상태바 · 하단 입력) 에서 체험할 수 있게 해주는 single-file REPL 런처. 서버/포트/네트워크 0.

## 원본 출처

| 항목 | 값 |
|---|---|
| 라이브러리 | [langgraph](https://github.com/langchain-ai/langgraph) (StateGraph · MemorySaver · interrupt · Command) + [Textual](https://github.com/Textualize/textual) (풀스크린 TUI) |
| 버전 | langgraph `1.0.10` · textual **`6.11.0`** (사내 미러 기준) |
| 라이선스 | 둘 다 MIT |
| 설치 참고 | `pip install --no-deps textual==6.11.0` *(사내 미러 규약)* |
| 관찰성 개념 참고 | LangSmith — span / latency / tokens 개념만 (001 의 Tracer 이식) |

## 기능 요약

- **`launch(graph, llm)` 한 줄 기동** — 사용자가 자신의 LangGraph 그래프 + LLM 어댑터를 넘기면 풀스크린 REPL 가동
- **Claude Code 스타일 레이아웃**:
  - 상단: `RichLog` 스크롤 가능 대화 이력 (user / assistant / tool / system 색상 구분)
  - 중단: 상태바 (thread_id · LLM 호출수 · 토큰 · latency · HITL 대기 여부)
  - 하단: 입력창 (플레이스홀더 + 슬래시 명령 힌트)
- **인라인 HITL** (Claude Code 스타일 — 팝업 모달 없음) — LLM 응답에 `ask_user={"type": ..., "question": ..., "options": ...}` 가 실리면 그래프가 `interrupt()` 로 멈추고, **입력창이 HITL 전용 위젯으로 인라인 전환**:
  - `"input"` (주관식) → **Input** 위젯 · 답변 타이핑 후 `Enter` 제출
  - `"choice"` (객관식) → **OptionList** · `↑↓` 로 이동 · `Enter` 로 선택
  - `"multi_choice"` (복수선택) → **커스텀 체크리스트** · `↑↓` 로 이동 · `Space` 로 토글 · `Enter` 로 제출
  - 위 셋 모두 `Esc` 로 취소 가능
  - 상단에 배너 (`🤚 객관식/복수선택/주관식  질문문구  ↑↓ ... Enter ... Esc`) 가 자동 노출
- **슬래시 명령 및 단축키** (macOS 친화 표준 키만 사용):
  - `/new` — 새 thread 로 리셋 (맥락 끊기, Tracer 는 유지) — `Ctrl+N`
  - `/trace` — 현재 트레이스를 `trace_<thread>_<ts>.html` 로 저장 — `Ctrl+T`
  - `/history` — 대화 이력을 다시 출력
  - `/tool` (또는 `/tools`, `/details`) — tool 호출 상세 모달 — **`Ctrl+O`**
  - `/help` — 도움말 — `F1`
  - `/quit` — 종료 (또는 `Ctrl+C`)
- **슬래시 팔레트** — 입력창에 `/` 입력하면 명령 목록이 바로 위에 인라인 힌트로 노출 (Claude Code 스타일). 타이핑하면 실시간 필터링, `Tab` 으로 첫 매치 자동완성, `Enter` 로 실행.
- **그래프 실행은 워커 스레드** — UI 가 프리즈되지 않음. 상태바에 `⏳ 응답 생성 중…` 표시
- **트레이스 뷰어** — 001 의 `Tracer` 이식. 저장된 HTML 은 self-contained (브라우저로 직접 열기 권장)

## 의존성

| 사용 여부 | 패키지 | 용도 |
|---|---|---|
| 필수 | `langgraph` | StateGraph · MemorySaver · interrupt · Command |
| 필수 | `textual` | 풀스크린 TUI 프레임워크 (Rich 기반) |
| 전이 | `rich` | textual 이 요구 — 포맷 출력 |
| 선택 | (없음) | 본체는 stdlib + 위 셋으로 완결 |

> **스택 적합성**: 사내 미러에 `textual==6.11.0` 이 등록되어 있습니다 (2026-04-24 확인). 단 **`pip install --no-deps textual==6.11.0` 로 설치** — 의존성 해결은 사내 미러 제약상 별도 경로로 이뤄집니다. `rich`, `pygments` 등 textual 의 전이 의존은 사내 미러의 다른 패키지 설치 과정에서 이미 존재하거나 따로 수동 설치. 이 프로젝트 코드는 textual 6.11.0 에서 테스트 통과 (전체 HITL + 슬래시 팔레트 + Ctrl+O Tool 상세 + /trace 시나리오). textual 을 쓸 수 없는 환경이면 `prompt_toolkit` 단독 버전인 **[004-langgraph-prompt-toolkit-repl](../004-langgraph-prompt-toolkit-repl/)** 을 사용하세요.

## 사용 예시

### 빠른 체험 (MockLLM + HITL 포함 그래프)

```bash
cd 003-langgraph-chat-repl
python examples/basic_usage.py
```

실행 후 입력창에 치트시트 문구를 넣어보세요:

- `안녕` — 일반 대화
- `12 + 7 + 100 계산해줘` — tool span (calculator) 가 트레이스에 기록됨
- `포트폴리오 추천해줘` — 객관식 HITL 모달 (RadioSet) 뜸
- `관심 자산군 여러 개 알려줘` — 복수선택 HITL 모달 (Checkbox) 뜸
- `상황을 구체적으로 설명해줘` — 주관식 HITL 모달 (TextArea) 뜸
- `/trace` — 트레이스 HTML 저장 (경로 표시)
- `/new` — 새 thread 로 리셋
- `/quit` — 종료

### 자신의 그래프로 REPL 띄우기

```python
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from repl import launch

# 사용자 정의 그래프 (HITL 쓰려면 interrupt() 호출하는 human 노드 포함)
g = StateGraph(MyState)
g.add_node("chat", my_chat_node)
g.add_edge(START, "chat")
g.add_edge("chat", END)
compiled = g.compile(checkpointer=MemorySaver())

# 사내 LLM 어댑터
from my_company.llm import InHouseLLM

launch(graph=compiled, llm=InHouseLLM())
```

### LLM 어댑터 인터페이스

사용자의 `llm` 객체는 `invoke(messages: list[dict]) -> dict` 만 구현하면 됩니다:

```python
class MyLLM:
    def invoke(self, messages: list[dict]) -> dict:
        # messages: [{"role": "user"|"assistant"|"tool", "content": "..."}, ...]
        # 반환: {"role": "assistant", "content": "..."}
        # HITL 이 필요하면 반환 dict 에 "ask_user": {"type": ..., "question": ..., "options": ...} 추가
        ...
```

`examples/basic_usage.py` 의 `MockLLM` 이 그대로 참고할 수 있는 최소 예시입니다.

## 파일 구조

```
003-langgraph-chat-repl/
├── README.md               # 이 문서
├── repl.py                 # single-file 본체 (Tracer + ChatEngine + Textual App + launch)
├── metadata.json
├── LICENSE                 # langgraph MIT
└── examples/
    └── basic_usage.py      # MockLLM + 최소 그래프 → REPL 기동
```

## 폐쇄망 친화 체크

| 항목 | 상태 |
|---|---|
| 외부 네트워크 호출 | ❌ 없음 (터미널 TTY 만 사용) |
| 새 서버 프로세스 | ❌ 없음 |
| 포트 오픈 | ❌ 없음 |
| 바이너리 영속화 | ❌ 없음 (트레이스만 HTML) |
| 외부 CDN | ❌ 없음 |
| 사내 반입 단위 | single `repl.py` |

## 알려진 제약 / 한계

- **MockLLM 은 시뮬레이터입니다.** 실제 응답 품질이 필요하면 사내 LLM 어댑터 연결 필수.
- **Textual 잠정 스택 추가**. 사내 미러 등록 확인 필요. 미등록 시 `prompt_toolkit` 기반으로 재작성 가능 (environment-adapter Skill).
- **터미널 요구사항**: ANSI 색 + 유니코드 박스 문자 지원되는 modern 터미널. `TERM=dumb` 같은 환경에선 동작 불가.
- **단일 라인 입력 (v1)**. 멀티라인 (Shift+Enter 개행 + Enter 전송) 은 v2 예정.
- **동시 사용자 분리 없음**. 이 REPL 은 "내 터미널에서 내가 쓰는" 용도. 팀 공유는 001 의 notebook 공유 또는 트레이스 HTML 반출로.
- **그래프의 동기/비동기**: graph.invoke() 를 워커 스레드로 호출. async 그래프 (`ainvoke`) 는 아직 미지원.

## 001 과의 관계

| 주제 | 001 (notebook-chatbot) | 003 (chat-repl) |
|---|---|---|
| 실행 환경 | JupyterLab 셀 output | 터미널 풀스크린 |
| UI 엔진 | ipywidgets | Textual |
| HITL UX | 셀 내부 위젯 교체 | 모달 화면 push |
| 그래프 정의 | 내장 (`chatbot.py` 안) | 사용자가 주입 (`launch(graph, llm)`) |
| 트레이스 | `save_trace("...")` + 노트북 인라인 렌더 | `/trace` 명령 → HTML 저장 + 경로 표시 |
| 공통 | 001 의 Tracer + HITL 페이로드 스키마 (`ask_user`) 그대로 이식 |
