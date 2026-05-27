# 007 - LangGraph Sidebar Chat (노트북 변수 인식 사이드바 챗봇)

> **한 줄 요약**: JupyterLab **우측 사이드바**에 떠 있으면서, **현재 노트북에 살아있는 변수**를 들여다보고 답하는 LangGraph 멀티턴 챗봇. 로컬 LLM 은 표준 라이브러리 `urllib` 로만 연결한다.

## 원본 출처

| 항목 | 값 |
|---|---|
| 라이브러리 | [langgraph](https://github.com/langchain-ai/langgraph) (StateGraph, MemorySaver 활용) |
| 버전 | `1.0.10` (폐쇄망 기본 스택 기준. 스모크 테스트는 1.2.2 — StateGraph/MemorySaver API 호환) |
| 라이선스 | MIT |
| 사이드바 패널 | jupyter [`sidecar`](https://github.com/jupyter-widgets/jupyterlab-sidecar) (BSD-3-Clause) — **선택 의존성**, 없으면 셀 인라인으로 폴백 |

## 이 변환물이 답하는 질문

> "Jupyter extension 챗봇이 **떠 있는 노트북의 변수를 볼 수 있나요?**"

**네.** 노트북 변수는 브라우저(프론트엔드)가 아니라 **커널**의 네임스페이스에 살아있습니다. 이 코드는 커널 안에서 돌기 때문에 `get_ipython().user_ns` 로 그 변수들을 직접 읽어 LLM 의 맥락으로 넣어줍니다. 무거운 TypeScript extension 빌드 없이, 순수 파이썬 + `sidecar` 위젯만으로 **우측 사이드바**에 챗봇을 띄웁니다.

## 기능 요약

- **우측 사이드바 패널**: `sidecar` 위젯으로 JupyterLab 우측 영역에 챗봇을 anchor. `sidecar` 가 없으면 현재 셀에 **인라인으로 폴백**(기능 동일).
- **노트북 변수 인식**: 매 턴마다 `get_ipython().user_ns` 를 읽어 변수 이름/타입/모양(shape·len·dtype 등)을 LLM 의 system 컨텍스트로 주입. 모듈·함수·클래스·`_` 변수·챗봇 객체 자신은 자동 제외.
- **프라이버시 기본값**: `include_values=False`(기본)면 **값은 빼고 메타데이터만** LLM 에 전달 → 고객/민감 데이터가 프롬프트로 새지 않습니다. 값까지 보여주려면 `include_values=True`.
- **변수 패널 + 새로고침**: 사이드바 상단에 "챗봇이 지금 보고 있는 변수" 표를 띄워 투명하게 확인. 셀에서 새 변수를 만들고 **변수 새로고침** 을 누르면 반영됩니다.
- **로컬 LLM 연결(urllib)**: `LocalOpenAILLM` 이 표준 라이브러리 `urllib.request` 만으로 로컬 OpenAI 호환 서버(`/v1/chat/completions`)를 호출. **openai SDK / requests / httpx 미사용**(폐쇄망 차단 패키지 회피). localhost 프록시 우회 포함.
- **MockLLM 내장**: 추론 서버 없이도 변수 인식 동작을 즉시 시연. system 으로 들어온 변수 목록을 그대로 인식해 응답합니다.
- **HTML 내보내기**: 대화 기록을 self-contained HTML 로 저장(data URL 다운로드 또는 파일). 바이너리 영속화 없음.

## 의존성

| 사용 여부 | 패키지 | 용도 |
|---|---|---|
| 필수 | `langgraph` | StateGraph(inspect→chat) + MemorySaver 멀티턴 |
| 선택 | `IPython` | `get_ipython().user_ns` 변수 인식 + 인라인 렌더 (노트북 밖이면 빈 네임스페이스) |
| 선택 | `ipywidgets` | 사이드바 챗 UI 위젯. 없으면 `chat()`/`export_html()` 프로그래매틱 사용 |
| 선택 | `sidecar` | JupyterLab 우측 패널. 없으면 셀 인라인 폴백 |

> 로컬 LLM 연결에는 **추가 패키지가 필요 없습니다** — 표준 라이브러리 `urllib` 만 씁니다.
>
> ⚠️ **폐쇄망 주의**: `sidecar`/`ipywidgets` 는 기본 스택(`default.yaml`)의 허용 목록에 아직 없습니다. 사내 미러 등록 여부를 확인하세요. `langgraph` 는 허용 스택에 포함됩니다.

## 사용 예시

가장 빠른 길은 `demo.ipynb` 를 JupyterLab 에서 여는 것입니다.

```python
# (A) 한 줄로 우측 사이드바에 띄우기 (서버 없이 MockLLM 으로 바로 체험)
from sidebar_chat import open_sidebar_chat
bot = open_sidebar_chat()

# (B) 실제 사내 로컬 LLM 연결
from sidebar_chat import LocalOpenAILLM, open_sidebar_chat
llm = LocalOpenAILLM(
    base_url="http://localhost:8000/v1",   # 사내 vLLM/Ollama/llama.cpp 주소
    model="qwen3",                          # 서버에 로드된 모델 이름
    # api_key="...",                        # 필요 시
)
bot = open_sidebar_chat(llm=llm)

# (C) 값까지 보여주고 싶을 때 (민감 데이터 주의)
bot = open_sidebar_chat(llm=llm, include_values=True)

# (D) UI 없이 프로그래매틱하게
from sidebar_chat import SidebarChat
bot = SidebarChat(llm=llm)
print(bot.chat("지금 메모리에 있는 DataFrame 들 요약해줘"))
bot.export_html("conversation.html")   # 업무망 반출용 HTML
```

이후 노트북 셀에서 `df = ...`, `x = ...` 처럼 변수를 만들고 사이드바에서 질문하면, 챗봇이 그 변수들을 보고 답합니다. (셀에서 새로 만든 변수는 사이드바의 **변수 새로고침** 으로 반영)

### 실행 환경 셋업

```bash
# 리포 루트의 통일 .venv 사용 (셋업: 루트 README 참고)
.venv/bin/jupyter lab 007-langgraph-sidebar-chat/demo.ipynb
```

## 동작 원리 (간단)

```
사용자 메시지
   │
   ▼
[langgraph] inspect 노드 ── get_ipython().user_ns 읽어 변수 요약 생성
   │                         (모듈/함수/_변수/챗봇객체 제외, max_vars 상한)
   ▼
[langgraph] chat 노드 ───── system(기본 프롬프트 + 변수 요약) + 대화이력 → LLM.invoke()
   │                         LLM = MockLLM | LocalOpenAILLM(urllib)
   ▼
assistant 응답  (MemorySaver + thread_id 로 멀티턴 유지)
```

변수 요약은 **매 턴 새로 계산**되어 항상 최신 노트북 상태를 반영하며, 대화 이력에는 누적되지 않습니다(프롬프트 비대화 방지).

## 알려진 제약 / 한계

- **MockLLM 은 데모용 에코** 입니다. `LocalOpenAILLM` 으로 사내 추론 서버를 연결해야 의미 있는 답변이 나옵니다.
- **`sidecar`/`ipywidgets` 미등록 가능성**: 폐쇄망 기본 스택에 없으므로 사내 미러 확인 필요. `sidecar` 없으면 셀 인라인 폴백, `ipywidgets` 까지 없으면 UI 대신 `chat()`/`export_html()` 만 사용.
- **HITL·트레이서 미포함**: 001 의 Human-in-the-loop(`ask_user`/`interrupt`)과 LangSmith 스타일 트레이서는 이 변환물 범위 밖입니다. 필요하면 [001](../001-langgraph-notebook-chatbot/) 을 참고/병행하세요.
- **변수 인식은 컨텍스트 주입 방식**: tool-calling 에 의존하지 않아 로컬 모델에서 안정적이지만, 변수 '값' 전체를 동적으로 조회하지는 않습니다(요약 + 짧은 미리보기까지).
- **`max_vars`(기본 40) 상한**: 변수가 많으면 잘립니다(프롬프트 폭주 방지).
- **메모리 휘발성**: 노트북 재시작 시 대화 이력 소멸. 반출은 `export_html` 로 HTML 스냅샷.
