# 007 - JupyterLab Sidebar Chatbot

> **한 줄 요약**: JupyterLab **우측 사이드바**에 탭으로 뜨는 챗봇. 프론트엔드는 정통 TypeScript labextension, 두뇌는 **langgraph 그래프(deepagents)** + **로컬 Ollama 또는 OpenAI 호환 모델**(실 OpenAI / 사내 vLLM). 프론트↔두뇌 통신은 **Jupyter Comm(커널 웹소켓)** 을 타서 **별도 포트·서버가 없고**, 브라우저와 커널이 다른 기계여도(원격/JupyterHub Pod, 8888만 노출) 동작합니다. 멀티턴은 langgraph 체크포인터(`thread_id`)가 관리하고, 응답은 **토큰 단위 스트리밍**으로 흘러와 마크다운으로 렌더, 도구·중간 단계는 접이식.

## 데모

![JupyterLab 사이드바 챗봇 데모](demo.webp)

> 실제 JupyterLab 에 띄워 Playwright 로 녹화한 화면입니다. ① `demo.ipynb` 의 **② 셀(`register_chatbot_comm()`)** 을 실행해 두뇌를 커널에 등록한 뒤 → ② 우측 **💬 Chatbot** 탭에 메시지 입력 → ③ 답변이 **토큰 단위로 또르르** 흘러오다 **마크다운 + 코드블록(복사 버튼)** 으로 마무리됩니다.
>
> 데모 두뇌는 **로컬 Ollama `qwen3.5:0.8b` 네이티브**(`register_chatbot_comm(provider="ollama")`). `api_key`/`base_url` 만 주면 실 OpenAI·사내 vLLM 으로 그대로 전환됩니다.
>
> *(위 영상은 이전 HTTP 전송 시절에 녹화됐습니다 — 그때는 두뇌 시작이 `start_graph_server()`(HTTP) 였고 지금은 `register_chatbot_comm()`(커널 Comm) 으로 바뀌었지만, 화면·UX 는 동일합니다.)*

## ⚠️ 성격

**1) 모델 호출은 온라인/개발 전용.**
두뇌의 **모델 호출**은 OpenAI 호환 REST(`/v1/chat/completions` 등) 또는 Ollama 네이티브입니다. 실 OpenAI 면 외부망, **사내 vLLM** 이면 내부망이고 `langchain-openai`/`openai`(httpx) 가 필요합니다(폐쇄망 패키지 정책 확인). **단, 프론트↔두뇌 전송 계층(Comm)은 네트워크에 독립적**이라, 모델 없이도(고정 텍스트) 연결만 점검할 수 있습니다(아래 *자가진단* 참고).

**2) 전송은 Jupyter Comm — 원격/Pod 에서도 동작.**
사이드바는 별도 HTTP 포트로 가지 않고, 노트북이 이미 쓰는 **커널 웹소켓**(`.../api/kernels/<id>/channels`)을 탑니다. JupyterHub 가 이미 프록시하는 그 채널이라 **브라우저와 커널이 다른 기계여도(ingress 로 8888만 열린 Pod)** 됩니다. (CORS·포트노출·서버변경 없음)

**3) 모델·엔드포인트 전환은 인자/env 만 — 코드 변경 0.**

| 환경 | `base_url` / `OPENAI_BASE_URL` | `api_key` / `OPENAI_API_KEY` | `model` / `OPENAI_MODEL` |
|---|---|---|---|
| 실 OpenAI(개발) | (비움) | sk-... | gpt-4o-mini |
| 사내 vLLM(운영) | https://<host>/v1 | (사내 키) | served-model-name |
| 로컬 Ollama | (`provider="ollama"`) | (불필요) | qwen3.5:0.8b |

**4) 키는 인자/환경변수로만.** 코드·노트북에 하드코딩 금지.

**5) single-file 아님 + wheel 무의존성.** 다수 파일 + npm 빌드 필요, 반입 단위는 `.whl`. **wheel 은 코드만** — deepagents·langchain-* 는 직접 설치(연결 점검만 할 땐 불필요).

## 아키텍처

```
브라우저: JupyterLab 페이지   (JupyterHub 면 https://<hub>/user/<id>/<server>/lab)
┌───────────────┬──────────────────┬─────────────────────┐
│  파일 탐색기   │   노트북/에디터    │  💬 챗봇 탭 (right)  │  ← 프론트 labextension (src/)
└──────┬────────┴──────────────────┴─────────┬───────────┘
       │ 셀에서 register_chatbot_comm() 실행     │ Jupyter Comm
       │                                         │   createComm("jlab_sidebar_chatbot")
       │                                         │ = 커널 웹소켓 .../api/kernels/<id>/channels
       │                                         │   (JupyterHub 가 이미 프록시 · 별도 포트 없음)
       ▼                                         ▼
   노트북 커널 (Python)  ◀──────────────  comm.py register_chatbot_comm()
        └─ graph.stream_turn(graph, thread_id, msg)   →  comm.send({token}…)→{done}
             └─ langgraph CompiledStateGraph.stream()
                  (deepagents.create_deep_agent) + InMemorySaver(thread_id=세션)
                       └─ ChatOpenAI(실 OpenAI / 사내 vLLM)  |  ChatOllama(네이티브)
```

- **프론트(`src/`)**: `kernelClient.ts` 가 **현재 노트북 커널**(`INotebookTracker`)에 Comm 을 열어 `{type:"message"}` 를 보내고, 커널이 흘려보낸 `{type:"token"}` 조각을 실시간으로 이어붙입니다. `{type:"done"}` 시점에 `markdown-it` + `highlight.js`(cherry-pick) 로 최종 재렌더 + `DOMPurify` sanitize. 코드블록 hover 시 "복사" 버튼, 도구·중간 단계는 접힌 `<details>`. (렌더 파이프라인은 `widget.ts`)
- **두뇌(`graph.py`)**: deepagents 그래프 + InMemorySaver. 모델은 `ChatOpenAI`/`ChatOllama` — 인자/env 만 바꾸면 OpenAI ↔ vLLM ↔ Ollama 동일 코드. `stream_turn()` 이 `graph.stream(stream_mode=["messages","values"])` 로 토큰을 흘려보냄.
- **전송(`comm.py`)**: `register_chatbot_comm()` 이 커널 `comm_manager` 에 target `jlab_sidebar_chatbot` 을 등록. 프론트가 보낸 메시지마다 `stream_turn()` 결과를 `comm.send` 로 흘립니다(커널→프론트: `ready`→`token`…→`done`, 그리고 `reset_ok`/`error`). HTTP 포트 없음.
- *(레거시)* **`server.py`**: 옛 HTTP 전송(`start_graph_server`/`start_test_server`, `/chat/stream` SSE 등). **현재 프론트는 사용하지 않습니다.** "브라우저 == 커널 같은 기계"가 보장되는 로컬에서만 유효하며, 원격/Pod 에서는 Comm 을 쓰세요.

### jupyter 서버 재시작 불필요
- 프론트 labextension 은 설치 + **브라우저 새로고침**만으로 뜸(jupyter 가 페이지 로드마다 labextensions 폴더 스캔).
- 두뇌는 노트북 셀에서 커널에 Comm 을 등록하는 방식이라 jupyter 재시작 불필요.
- 사이드바는 **현재 활성 노트북의 커널**에 붙습니다 — `register_chatbot_comm()` 을 그 노트북 셀에서 실행하세요.

## 원격/JupyterHub Pod 에서 동작하는 이유
브라우저가 닿을 수 있는 입구는 **JupyterLab(8888)** 하나뿐인 환경(ingress/route 로 8888만 노출)이 흔합니다. 옛 HTTP 전송은 두뇌를 `127.0.0.1:8765` 로 띄워, 브라우저가 *자기 기계의* 8765 를 보게 되어 `ERR_CONNECTION_REFUSED` 로 실패했습니다. **Comm 은 노트북이 이미 쓰는 커널 채널(8888 경유, Hub 가 프록시)** 을 타므로 그 문제가 사라집니다. `docker-repro/`(8888만 publish 한 컨테이너)로 이 토폴로지를 재현·검증할 수 있습니다.

## 원본 출처

| 항목 | 값 |
|---|---|
| UI 패턴 | [jupyterlab/extension-examples · shout-button-message](https://github.com/jupyterlab/extension-examples/tree/main/shout-button-message) |
| 두뇌 | [deepagents](https://github.com/langchain-ai/deepagents) `create_deep_agent` → langgraph + InMemorySaver |
| 모델 어댑터 | `langchain-openai` `ChatOpenAI` / `langchain-ollama` `ChatOllama` |
| 전송 | Jupyter Comm (`@jupyterlab/services` `createComm` ↔ ipykernel `comm_manager`) |
| 타겟 | JupyterLab **4.5.x** |
| 라이선스 | BSD-3-Clause |

## 기능 요약
- 우측 사이드바 탭(shout 예제 패턴), 채팅 UI(Enter 전송 · Shift+Enter 줄바꿈 · 새 대화).
- **커널 Comm 전송** — 프론트가 현재 노트북 커널에 Comm 을 열어 통신. 별도 포트·CORS·서버변경 없음. **원격/Pod(8888만 열림)에서도 동작**. 커널 미등록/없음이면 친절한 안내(셀에서 `register_chatbot_comm` 먼저 실행).
- **토큰 스트리밍** — 답변 토큰을 `comm.send` 로 실시간 표시, 끝(`done`)에 권위 있는 `{answer, steps}` 로 마크다운 최종 렌더.
- **마크다운 답변 자체 렌더** (`markdown-it` + `highlight.js` cherry-pick + `DOMPurify` sanitize) — `jp-RenderedHTMLCommon` 큰 여백 안 거치고 컴팩트하게.
- **코드블록 복사 버튼** — hover 시 우측 상단, `clipboard` API + `execCommand` 폴백(HTTP 폐쇄망 대응).
- **도구·중간 단계 접이식** — `{answer, steps}` 구조, 기본 접힘.
- **멀티턴** — langgraph `InMemorySaver` + `thread_id`(=세션). `새 대화` 는 thread 분기(`reset`).
- **모델·엔드포인트 교체** — `register_chatbot_comm(...)` 인자 또는 env(`OPENAI_*`/`OLLAMA_*`/`CHAT_PROVIDER`). 코드 변경 0.

## 자가진단 (LLM·패키지 없이 연결만 점검)

`comm_selftest_cell.py` 는 `register_chatbot_comm` 도 deepagents 도 LLM 도 쓰지 않고, **표준 라이브러리(`re`) + IPython/ipykernel**(`get_ipython().kernel.comm_manager`) 만으로 Comm target 을 등록해 **고정 텍스트**를 돌려줍니다. 사내망에서 "사이드바가 커널 Comm 으로 붙는지(전송 경로)"만 분리해 점검할 때, 그 파일 내용을 **노트북 셀에 통째로 복붙**해 실행하고 💬 에서 메시지를 보내면 됩니다(의존성 0). demo.ipynb 의 **"(선택) 자가진단"** 셀로도 들어 있습니다.

## 의존성 (wheel 에는 미포함 — 직접 설치)

```bash
# 연결 점검(자가진단)만 할 거면 아무 것도 필요 없음 — wheel 만 설치하면 됨.
# 실제 모델을 쓰려면:
pip install deepagents langchain-openai   # ChatOpenAI (provider="openai" 기본 — 실 OpenAI / 사내 vLLM)
pip install langchain-ollama              # (선택) provider="ollama" — 로컬 Ollama 네이티브(키 불필요)
```

| 구분 | 패키지 | 비고 |
|---|---|---|
| 두뇌 | `deepagents` | langgraph 그래프(create_deep_agent). langchain·langgraph 등은 deepagents 가 자동 설치 |
| 모델 어댑터 (openai) | `langchain-openai`(→`openai`) | `provider="openai"`(기본) — 실 OpenAI / 사내 vLLM(`/v1`) |
| 모델 어댑터 (ollama) | `langchain-ollama` *(선택)* | `provider="ollama"` — Ollama 네이티브(`/api/chat`)·키 불필요 |
| 프론트(번들됨) | `@jupyterlab/application`·`notebook`·`services`·`ui-components`, `@lumino/widgets`·`messaging`, `markdown-it`·`highlight.js`(cherry-pick)·`dompurify` | 빌드 시. wheel 에 모두 번들됨 |

## 사용 예시

### 1) 빌드 (dev 환경)
```bash
jlpm install
jlpm run build:prod
pip wheel . -w dist --no-deps      # 무의존성 .whl  (또는: uv build --wheel)
```

### 2) 설치 & 실행 (jupyter 재시작 없이)
```bash
pip install jlab_sidebar_chatbot-*.whl       # 또는 노트북: %pip install dist/*.whl
# 실제 모델을 쓸 거면 deepagents + langchain-openai(또는 langchain-ollama) 도 설치
jupyter lab        # 브라우저 새로고침 → 우측 💬 탭
```
노트북 셀에서 두뇌를 커널에 등록 — 방식 택1:

**(a) 로컬 Ollama (키 불필요, 가장 빠르게)**:
```python
from jlab_sidebar_chatbot import register_chatbot_comm
register_chatbot_comm(provider="ollama", model="qwen3.5:0.8b")
# 사전: ollama 실행 + pip install langchain-ollama. (모델/주소는 OLLAMA_MODEL/OLLAMA_BASE_URL env 로도)
```

**(b) 사내 vLLM (OpenAI 호환 /v1)**:
```python
from jlab_sidebar_chatbot import register_chatbot_comm
register_chatbot_comm(
    api_key="<사내 vLLM 키>",          # 또는 OPENAI_API_KEY env (하드코딩 금지)
    base_url="https://<사내 vllm host>/v1",
    model="<vLLM 의 served-model-name>",
)
```
> 인자가 env 보다 우선합니다. 키를 노트북에 적기 싫으면 `getpass.getpass()` 로 받으세요.
> env 방식(`OPENAI_API_KEY`/`OPENAI_BASE_URL`/`OPENAI_MODEL` 설정 후 `register_chatbot_comm()`)도 됩니다.

**(c) 모델 없이 전송만 점검(stub)**:
```python
from jlab_sidebar_chatbot import register_chatbot_comm
register_chatbot_comm(base_url=None)   # 고정 텍스트(stub). deepagents 없이도 동작 — LLM 호출 0
# 패키지 함수도 없이 점검하려면 comm_selftest_cell.py 복붙 (의존성 0)
```

> 멈추기: `from jlab_sidebar_chatbot import unregister_chatbot_comm; unregister_chatbot_comm()`

### 3) 시스템 프롬프트·도구 교체
```python
from jlab_sidebar_chatbot import register_chatbot_comm
register_chatbot_comm(
    provider="ollama", model="qwen3.5:0.8b",
    system_prompt="너는 SQL 전문가야. 쿼리 위주로 답해.",
    # tools=[my_tool_fn, ...]   # 필요하면 deepagents 도구 추가
)
```
> 다른 langchain 챗모델로 바꾸려면 `graph.py` 의 `_build_chat_model` 부분을 교체.

### 4) 재현/테스트 환경 (`docker-repro/`)
8888 만 노출한 컨테이너로 "원격 Pod" 토폴로지를 재현합니다(자세한 건 `docker-repro/README.md`):
```bash
docker compose -f docker-repro/docker-compose.yml up -d --build
# http://127.0.0.1:8888/user/<id>/<server>/lab?token=demo
```

## 빌드 시 주의 (lockfile 없는 환경에서 필요한 3가지 핀)
1. **`.yarnrc.yml` `nodeLinker: node-modules`** — jlpm 기본 PnP 는 `@jupyterlab/builder`(webpack)와 비호환.
2. **`tsconfig.json` `skipLibCheck: true`** — 일부 의존성 `.d.ts` 가 최신 `@types/node` 와 충돌.
3. **`package.json` `resolutions.webpack = 5.88.2`** — builder 의 `license-webpack-plugin@2.x` 가 최신 webpack 에서 깨짐.

## 알려진 제약/한계점
- **모델: OpenAI 호환 / Ollama 필요** — `provider="openai"` 면 `api_key` 필수(실 OpenAI=외부망, 사내 vLLM=내부망, httpx). `provider="ollama"` 는 키 불필요. *(연결 점검만 할 땐 모델 자체가 불필요.)*
- **tool calling 지원 모델 필요** — deepagents 가 `write_todos`/`write_file` 등 도구를 부릅니다. 사내 vLLM 이면 모델이 OpenAI 호환 tool-call(예: Llama-3.1-instruct, Qwen-함수콜링 템플릿)을 제대로 내보내야 단계가 정상 — 안 되면 답변만 오고 steps 가 비어 보입니다.
- **wheel 무의존성** — 실제 모델을 쓰려면 deepagents·langchain-openai(또는 langchain-ollama) 직접 설치.
- **현재 노트북 커널에 종속** — 사이드바는 활성 노트북의 커널에 붙습니다. `register_chatbot_comm()` 을 그 커널 셀에서 실행해야 하고, 커널 재시작 시 다시 등록해야 합니다. (안 했으면 💬 가 "셀에서 먼저 등록하세요" 안내)
- **대화 기록은 메모리 한정** — 커널 재시작 / `unregister_chatbot_comm()` / `새 대화(reset)` 시 소실.
- **single-file 아님** — 다수 파일 + npm 빌드. 반입 단위는 `.whl`.
- **프론트 변경 시 재빌드 필요** — `src/*.ts` 를 고치면 `jlpm install && jlpm run build:prod` 후 wheel 재빌드(파이썬만 고치면 커널 재등록으로 충분).
- **(레거시) `server.py` HTTP 전송**은 "브라우저==커널 같은 기계"가 전제 — 원격/Pod 에서는 동작하지 않습니다(그래서 Comm 으로 전환). 현재 프론트는 사용 안 함.
- **JupyterLab 4 전용**.
