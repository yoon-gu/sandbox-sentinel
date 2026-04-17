# 001 - LangGraph Notebook Chatbot

> **한 줄 요약**: 폐쇄망 Jupyter 노트북에서 돌리는 LangGraph 멀티턴 챗봇 + LangSmith 스타일의 self-contained HTML 관찰성(observability) 뷰어

## 원본 출처

| 항목 | 값 |
|---|---|
| 라이브러리 | [langgraph](https://github.com/langchain-ai/langgraph) (StateGraph, MemorySaver 활용) |
| 버전 | `>=0.2,<1.0` |
| 라이선스 | MIT |
| 관찰성 개념 참고 | [LangSmith](https://smith.langchain.com/) — **코드 복제 아님**, span/latency/tokens 개념만 참고 재구현 |

## 기능 요약

- **멀티턴 대화**: `langgraph.checkpoint.memory.MemorySaver` + `thread_id` 기반. `bot.chat()` 을 반복 호출하면 같은 thread의 맥락이 유지됩니다.
- **간이 트레이서(LangSmith 대체)**: chain/LLM/tool 레벨의 계층형 span을 메모리에 수집합니다. 각 span은 `parent_id`, 시작/종료 시각, 입출력, 토큰 수, latency, 에러를 포함합니다.
- **self-contained HTML 뷰어**: 수집된 span을 `<script type="application/json">`로 임베드한 단일 HTML로 저장합니다. 외부 `fetch`, `<script src>`, `<link href>` **일절 없음** — 업무망에서도 파일 하나만으로 동작합니다.
- **Jupyter 친화 API**:
  - `bot.show_history()` — 대화를 채팅 풍선 UI로 셀 안에 표시
  - `bot.show_trace()` — 트레이스 뷰어를 셀 안에 인라인 표시
  - `bot.save_trace("trace.html")` — 파일로 내보내기 (반출용)
- **Mock LLM 포함**: 외부 모델 없이 워크플로 전체를 바로 돌려볼 수 있는 에코 스타일 시뮬레이터. 실제 사내 LLM 어댑터로 손쉽게 교체할 수 있습니다.
- **샘플 도구(calculator)**: 사용자 입력에 "계산", "더하기", 숫자 등이 있으면 합계를 구해주는 아주 단순한 tool 노드가 들어 있어, tool span이 트레이스 뷰어에 어떻게 표시되는지 바로 확인할 수 있습니다.

## 의존성

| 사용 여부 | 패키지 | 용도 |
|---|---|---|
| 필수 | `langgraph` | StateGraph, MemorySaver, START/END |
| 선택 | `IPython` | 노트북 인라인 렌더 (`show_trace` / `show_history`). 미설치 시 `save_trace()` / `print` 로 대체 |
| 선택 | `ipywidgets` | 셀 output 안에서 동작하는 인터랙티브 채팅 UI (`chat_ui`). 미설치 시 `chat()` + `show_history()` 조합으로 대체 |

> `numpy`, `pandas`, `torch`, `transformers` 등은 **사용하지 않습니다**. 원하는 경우 실제 LLM 어댑터에서만 추가로 사용하세요.

## 사용 예시

가장 빠른 길은 `examples/demo.ipynb` 를 JupyterLab 에서 여는 것입니다. 노트북 최상단의 **0. 인터랙티브 채팅 UI** 셀 하나만 실행해도 셀 output 안에서 멀티턴 대화 + 트레이스 조회가 바로 가능합니다.

```bash
cd 001-langgraph-notebook-chatbot
jupyter lab examples/demo.ipynb
```

노트북에서 쓰이는 핵심 API:

```python
from chatbot import Chatbot

bot = Chatbot()          # 새 thread_id + MockLLM

# (A) 인터랙티브 UI — 셀 하나로 멀티턴 대화
bot.chat_ui()            # 입력창 + 보내기/트레이스/리셋 버튼 + 대화 풍선

# (B) 프로그래매틱 방식 — 셀 단위로 쪼개서 쓰기
bot.chat("안녕")                    # 턴 1
bot.chat("방금 질문 기억해?")         # 턴 2 — 같은 thread, 맥락 유지
bot.show_history()                  # 셀 안에 대화 풍선으로 표시
bot.show_trace()                    # 셀 안에 트레이스 뷰어 표시
bot.save_trace("trace.html")        # 업무망 반출용 HTML 내보내기
```

### 실제 사내 LLM으로 교체하기

`MockLLM` 을 치워내고, 아래 인터페이스를 따르는 어댑터를 주입하세요.

```python
class MyLocalLLM:
    def __init__(self, tracer=None):
        self.tracer = tracer
    def invoke(self, messages: list[dict]) -> dict:
        # messages: [{"role": "user"|"assistant"|"tool", "content": "..."}, ...]
        # 반환: {"role": "assistant", "content": "..."}
        ...

bot = Chatbot(llm=MyLocalLLM())
```

어댑터 내부에서 `self.tracer.span("LLM:xxx", kind="llm", inputs=messages)` 블록으로 감싸주면 트레이스 뷰어에 토큰/latency가 함께 표시됩니다 (`MockLLM` 구현을 그대로 참고하세요).

## 트레이스 뷰어 화면 구성

- 상단 통계: 총 span 수, LLM 호출 수, 누적 입/출력 토큰, 최상위 누적 latency
- 계층 트리: chain(주황) → LLM(보라) / tool(초록) 순으로 들여쓰기
- 각 span 헤더 클릭 → inputs/outputs JSON, metadata 펼침
- latency 바: 최대 latency span을 100% 로 두고 상대 비율을 표시

## 파일 구조

```
001-langgraph-notebook-chatbot/
├── README.md               # 이 문서
├── chatbot.py              # single-file 본체 (Tracer + MockLLM + Graph + HTML 템플릿)
├── metadata.json
├── LICENSE                 # 원본(langgraph) MIT 라이선스 복제본
└── examples/
    └── demo.ipynb          # 노트북 데모 (최상단에 인터랙티브 chat_ui 셀, 이후 단계별 셀 예시)
```

## 알려진 제약 / 한계

- **MockLLM은 실제 언어모델이 아닙니다.** 에코 스타일의 시뮬레이터이므로, 응답 품질이 아니라 *워크플로와 트레이스 구조*를 확인하는 용도로만 사용하세요. 사내 실제 LLM 어댑터를 연결해야 의미 있는 답변이 나옵니다.
- **토큰 수는 공백 기준 단어 수 근사**입니다. 실제 토크나이저 수치가 필요하면 어댑터 쪽에서 주입하세요.
- **영속화는 HTML export 만 지원**합니다 (CLAUDE.md 정책). SQLite/pickle 기반 checkpointer는 사용하지 않습니다. 노트북 프로세스를 재시작하면 `MemorySaver` 의 대화 이력도 사라지므로, 반출이 필요한 경우 `save_trace` / `_render_history_html` 로 HTML에 스냅샷을 남기세요.
- **LangSmith의 웹 기반 팀 공유/비교/검색 기능은 없습니다.** 본 뷰어는 "한 번의 실행 스냅샷" 단위의 HTML입니다.
- **비동기 실행(`ainvoke`)은 별도 검증하지 않았습니다.** 동기 `invoke` 기준으로 동작합니다. 비동기로 확장할 경우 ContextVar 전파가 async 태스크 경계에서 예상대로 유지되는지 확인 필요.
