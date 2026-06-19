# 007 - JupyterLab Sidebar Chatbot

> **한 줄 요약**: JupyterLab **우측 사이드바**에 탭으로 뜨는 챗봇. 프론트엔드는 정통 TypeScript labextension, 두뇌는 **langgraph 그래프(deepagents)** + **OpenAI 호환 모델**(실 OpenAI / 사내 vLLM / 로컬 Ollama). 멀티턴은 langgraph 체크포인터(`thread_id`)가 관리하고, 응답은 마크다운으로 렌더, 도구·중간 단계는 접이식.

## 데모

![JupyterLab 사이드바 챗봇 데모](demo.webp)

> 실제 JupyterLab(4.5.6) 을 띄워 녹화한 화면입니다. ① 노트북에서 `start_graph_server()` 셀 한 번 실행 → ② 우측 **💬 Chatbot** 탭 → ③ 질문에 **마크다운 + 코드블록(복사 버튼)** 으로 답변 → ④ 도구를 쓰는 작업은 **`🔧 도구·중간 단계`** 를 클릭해 펼쳐 확인. (데모 모델은 로컬 Ollama `qwen3.5`, OpenAI 호환 엔드포인트로 연결 — env 만 바꾸면 사내 vLLM 동일)

## ⚠️ 성격

**1) 온라인/개발 전용 (네트워크 의존).**
모델 호출은 OpenAI 호환 REST(`/v1/chat/completions` 등). 실 OpenAI 면 외부망, **사내 vLLM** 으로 가면 내부망입니다. 어느 쪽이든 `openai`/`langchain-openai`(httpx 기반) 가 필요해 폐쇄망 패키지 정책 확인이 필요합니다.

**2) 환경 전환은 env 만 — 코드 변경 0.**

| 환경 | `OPENAI_BASE_URL` | `OPENAI_API_KEY` | `OPENAI_MODEL` |
|---|---|---|---|
| 실 OpenAI(개발) | (비움) | sk-... | gpt-4o-mini |
| 사내 vLLM(운영) | https://<host>/v1 | (사내 키) | served-model-name |
| 로컬 vLLM/Ollama | http://localhost:8000/v1 | dummy | served-model |

**3) 키는 환경변수로만.** 코드/노트북에 하드코딩 금지.

**4) single-file 아님 + wheel 무의존성.** 다수 파일 + npm 빌드 필요, 반입 단위는 `.whl`. **wheel 은 코드만** — deepagents·langchain-openai 는 직접 설치.

## 아키텍처

```
브라우저: JupyterLab 페이지 (localhost)
┌───────────────┬──────────────────┬─────────────────────┐
│  파일 탐색기   │   노트북/에디터    │  💬 챗봇 탭 (right)  │  ← 프론트 labextension (src/)
└──────┬────────┴──────────────────┴─────────┬───────────┘
       │ 셀에서 start_graph_server() 실행       │ fetch http://127.0.0.1:8765/chat
       ▼                                        ▼
   노트북 커널 (Python)                          server.py (stdlib http + CORS, 얕은 전송)
                                                  └─ graph.run_turn(graph, thread_id, msg)
                                                       └─ langgraph CompiledStateGraph
                                                            (deepagents.create_deep_agent)
                                                            + InMemorySaver(thread_id=세션)
                                                                 └─ ChatOpenAI(base_url=…)
                                                                      → 실 OpenAI / 사내 vLLM
```

- **프론트(`src/`)**: 답변은 `markdown-it` + `highlight.js`(cherry-pick) 로 자체 렌더, `DOMPurify` 로 sanitize. 코드블록 hover 시 우측 상단에 "복사" 버튼. 도구·중간 단계는 기본 접힌 `<details>`(클릭 시 펼침).
- **두뇌(`graph.py`)**: deepagents 그래프 + InMemorySaver. 모델은 `ChatOpenAI(base_url=env, api_key=env, model=env)` — env 만 바꾸면 OpenAI ↔ vLLM ↔ Ollama 동일 코드.
- **서빙(`server.py`)**: 얕은 HTTP 전송(`/chat`·`/reset`·`/health`). `/chat` 응답은 `{answer, steps}`.

### jupyter 서버 재시작 불필요
- 프론트 labextension 은 설치 + **브라우저 새로고침**만으로 뜸(jupyter 가 페이지 로드마다 labextensions 폴더 스캔).
- 두뇌는 노트북 셀에서 띄우는 localhost 서버라 jupyter 재시작 불필요.
- **전제**: 브라우저 `127.0.0.1` == 커널 `127.0.0.1` (로컬/컨테이너/포트포워딩).

## 원본 출처

| 항목 | 값 |
|---|---|
| UI 패턴 | [jupyterlab/extension-examples · shout-button-message](https://github.com/jupyterlab/extension-examples/tree/main/shout-button-message) |
| 두뇌 | [deepagents](https://github.com/langchain-ai/deepagents) `create_deep_agent` → langgraph + InMemorySaver |
| 모델 어댑터 | `langchain-openai` `ChatOpenAI` (OpenAI 호환 — 실 OpenAI / vLLM / Ollama) |
| 타겟 | JupyterLab **4.5.x** |
| 라이선스 | BSD-3-Clause |

## 기능 요약
- 우측 사이드바 탭(shout 예제 패턴), 채팅 UI(Enter 전송 · Shift+Enter 줄바꿈 · 새 대화).
- **마크다운 답변 자체 렌더** (`markdown-it` + `highlight.js` cherry-pick + `DOMPurify` sanitize) — JupyterLab 의 `jp-RenderedHTMLCommon` 큰 여백 안 거치고 챗에 맞게 컴팩트하게.
- **코드블록 복사 버튼** — hover 시 우측 상단에 등장, `clipboard` API + `execCommand` 폴백(HTTP 폐쇄망 대응).
- **도구·중간 단계 접이식** — `{answer, steps}` 구조. 단계는 기본 접힘, 클릭 시 펼침.
- **멀티턴** — langgraph `InMemorySaver` + `thread_id`(=세션). `새 대화` 는 thread 분기.
- **모델·엔드포인트 교체** — 환경변수만 (`OPENAI_BASE_URL`/`OPENAI_API_KEY`/`OPENAI_MODEL`). 코드 변경 0.

## 의존성 (wheel 에는 미포함 — 직접 설치)

```bash
pip install deepagents langchain-openai
#            └ 그래프      └ ChatOpenAI (OpenAI 호환 — 실 OpenAI / 사내 vLLM / Ollama)
```

| 구분 | 패키지 | 비고 |
|---|---|---|
| 두뇌 | `deepagents` | langgraph 그래프(create_deep_agent). langchain·langchain-anthropic 등은 deepagents 가 의존성으로 자동 설치 |
| 모델 어댑터 | `langchain-openai`(→`openai`) | OpenAI 호환 — base_url 만 바꾸면 vLLM/Ollama |
| 프론트(번들됨) | `@jupyterlab/application`·`ui-components`, `@lumino/widgets`·`messaging`, `markdown-it`·`highlight.js`(cherry-pick)·`dompurify` | 빌드 시. wheel 에 모두 번들됨 |

## 사용 예시

### 1) 빌드 (dev 환경)
```bash
jlpm install
jlpm run build:prod
pip wheel . -w dist --no-deps      # 무의존성 .whl
```

### 2) 설치 & 실행 (jupyter 재시작 없이)
```bash
pip install deepagents langchain-openai
pip install jlab_sidebar_chatbot-*.whl       # 또는 노트북: %pip install dist/*.whl
```
환경변수 설정 (사내 vLLM 예시):
```bash
export OPENAI_API_KEY=<사내 키>
export OPENAI_BASE_URL=https://<사내 vllm host>/v1
export OPENAI_MODEL=<vllm 의 served-model-name>
jupyter lab        # 새로고침 → 우측 💬 탭
```
노트북 셀에서 두뇌 서버 시작 — 두 가지 방식 중 택1:

**(a) 환경변수 방식** (셀에 키 노출 안 함 — 권장):
```python
from jlab_sidebar_chatbot import start_graph_server
start_graph_server()    # OPENAI_API_KEY/_BASE_URL/_MODEL 이 jupyter env 에서 상속됨
```

**(b) 인자로 한 줄에 — 사내 vLLM 빠르게 전환할 때**:
```python
from jlab_sidebar_chatbot import start_graph_server
start_graph_server(
    api_key="<사내 vLLM 키>",
    base_url="https://<사내 vllm host>/v1",
    model="<vLLM 의 served-model-name>",
)
```
> 인자가 env 보다 우선합니다. 키를 노트북에 직접 적기 싫으면 `getpass.getpass()` 로 받으세요.

### 3) 시스템 프롬프트·도구 교체
```python
from jlab_sidebar_chatbot import start_graph_server
start_graph_server(
    system_prompt="너는 SQL 전문가야. 쿼리 위주로 답해.",
    # tools=[my_tool_fn, ...]   # 필요하면 deepagents 도구 추가
)
```
> 다른 langchain 챗모델로 바꾸려면 `graph.py` 의 `ChatOpenAI` 부분을 그 클래스로 교체.

## 빌드 시 주의 (lockfile 없는 환경에서 필요한 3가지 핀)
1. **`.yarnrc.yml` `nodeLinker: node-modules`** — jlpm 기본 PnP 는 `@jupyterlab/builder`(webpack)와 비호환.
2. **`tsconfig.json` `skipLibCheck: true`** — 일부 의존성 `.d.ts` 가 최신 `@types/node` 와 충돌.
3. **`package.json` `resolutions.webpack = 5.88.2`** — builder 의 `license-webpack-plugin@2.x` 가 최신 webpack 에서 깨짐.

## 알려진 제약/한계점
- **OpenAI 호환 엔드포인트 필수** — `OPENAI_API_KEY` 필수. 실 OpenAI 는 외부망, 사내 vLLM 은 내부망. 어느 쪽이든 httpx 사용.
- **tool calling 지원 모델 필요** — deepagents 가 `write_todos`/`write_file` 등 도구를 부릅니다. 사내 vLLM 이면 모델이 OpenAI 호환 tool-call(예: Llama-3.1-instruct, Qwen-함수콜링 템플릿)을 제대로 내보내야 단계가 정상 — 안 되면 답변만 오고 steps 가 비어 보입니다.
- **wheel 무의존성** — deepagents·langchain-openai 를 직접 설치해야 동작 (langchain-anthropic 등은 deepagents 가 자동으로 끌어옴).
- **localhost 전제** — 브라우저 `127.0.0.1` == 커널 기계.
- **셀 한 줄 실행 필요** — 매 커널 세션마다 `start_graph_server()`. 포트 기본 8765 (server.py / handler.ts 상수 일치).
- **single-file 아님** — 다수 파일 + npm 빌드.
- **대화 기록은 메모리 한정** — 커널 재시작 / `stop_graph_server()` / `/reset` 시 소실.
- **JupyterLab 4 전용**. 스트리밍 응답은 범위 외.
