# 007 - JupyterLab Sidebar Chatbot

> **한 줄 요약**: JupyterLab **우측 사이드바**에 탭으로 뜨는 챗봇. 프론트엔드는 TypeScript labextension, 두뇌는 **deepagents 가 만든 langgraph 그래프**이고, **LangGraph 생태계 네이티브 서빙(`langgraph dev` / LangGraph Server)** 으로 띄웁니다. 멀티턴(thread 영속화)은 LangGraph 플랫폼이 제공합니다.

## ⚠️ 두 가지 중요한 성격

**1) 온라인/개발 전용 — 폐쇄망 배포용이 아닙니다.**
기본 두뇌가 **Claude API(api.anthropic.com)** 를 호출합니다. 리포의 폐쇄망 원칙(외부망 금지·로컬 LLM)과 다른, **로컬 개발/데모** 성격입니다. 폐쇄망에서 쓰려면 `graph.py` 의 `make_graph()` 에서 `ChatAnthropic` 를 사내 LLM 용 langchain 챗모델로 교체하세요. API 키는 **환경변수 `ANTHROPIC_API_KEY` 로만** 읽습니다(코드/노트북 하드코딩 금지).

**2) single-file 이 아니고, wheel 에 런타임 의존성이 없습니다.**
JupyterLab 익스텐션은 TypeScript + npm 빌드가 필요해 반입 단위가 빌드된 wheel(`.whl`)입니다. 그리고 **wheel 은 코드만** 담습니다(의존성 0) — 폐쇄망에서 아래 의존성을 직접 설치하세요. 가벼운 대안은 **005**·**006** 참고.

## 아키텍처

```
브라우저: JupyterLab 페이지 (localhost)
┌───────────────┬──────────────────┬─────────────────────┐
│  파일 탐색기   │   노트북/에디터    │  💬 챗봇 탭 (right)  │  ← 프론트 labextension (src/)
└───────────────┴──────────────────┴─────────┬───────────┘
                                              │ fetch (LangGraph API, CORS)
                                              ▼  http://127.0.0.1:2024
                       LangGraph Server  (`langgraph dev`)
                       ┌────────────────────────────────────────┐
                       │ POST /threads               (thread 생성) │
                       │ POST /threads/{id}/runs/wait (한 턴 실행)  │
                       │   + thread 영속화(플랫폼 제공) = 멀티턴     │
                       │   graph = graph.py:make_graph             │
                       │     └ deepagents.create_deep_agent        │
                       │          └ ChatAnthropic (Claude)         │
                       └────────────────────────────────────────┘
```

- **프론트(`src/`)**: 우측 사이드바 Widget. 첫 입력 시 thread 를 만들고(`/threads`), 이후 `/threads/{id}/runs/wait` 로 매 턴 실행. `새 대화` = 새 thread.
- **두뇌(`jlab_sidebar_chatbot/graph.py`)**: `make_graph()` 가 deepagents 로 langgraph 그래프를 만듭니다(체크포인터 없음 — 플랫폼이 thread 영속화 제공). 직접 만든 Adapter/Mock/Brain/HTTP 서버는 없습니다.
- **서빙**: `langgraph.json` 이 `make_graph` 를 가리키고, `langgraph dev` 가 LangGraph 플랫폼 API 로 서빙합니다.

### jupyter 서버 재시작 불필요
- 프론트 labextension 은 설치 + **브라우저 새로고침**만으로 뜹니다(jupyter 가 페이지 로드마다 labextensions 폴더 스캔).
- 두뇌(LangGraph Server)는 jupyter 와 **별개 프로세스**(`langgraph dev`)라 jupyter 재시작과 무관합니다.
- **전제**: 브라우저의 `127.0.0.1` 과 `langgraph dev` 서버가 **같은 기계**여야 합니다(로컬·컨테이너·SSH 포트포워딩). 원격 호스트명 접속이면 닿지 않습니다.

## 원본 출처

| 항목 | 값 |
|---|---|
| UI 패턴 | [jupyterlab/extension-examples · shout-button-message](https://github.com/jupyterlab/extension-examples/tree/main/shout-button-message) — `Widget` + `app.shell.add(widget,'right')` + `onAfterAttach`/`onBeforeDetach` |
| 두뇌·서빙 | [deepagents](https://github.com/langchain-ai/deepagents) `create_deep_agent` → langgraph 그래프 / [LangGraph Server](https://langchain-ai.github.io/langgraph/cloud/) (`langgraph dev`) 네이티브 서빙 |
| 타겟 | JupyterLab **4.5.x** |
| 라이선스 | BSD-3-Clause |

## 기능 요약

- **우측 사이드바 탭** (shout 예제 패턴), 탭 안은 챗 UI.
- **LangGraph 네이티브 서빙** — `langgraph dev` 가 그래프를 thread/run API 로 서빙. 프론트는 그 API 호출.
- **멀티턴** — LangGraph 플랫폼의 thread 영속화. `새 대화` = 새 thread.
- **채팅 UI** — 말풍선 · 입력창(Enter 전송 / Shift+Enter 줄바꿈) · `새 대화`. 라이트/다크 자동 적응.
- **모델·프롬프트·도구 교체** — `graph.py` 의 `make_graph()` 수정(`ChatAnthropic`/`system_prompt`/`tools`).

## 의존성 (wheel 에는 미포함 — 직접 설치)

wheel 은 **코드만** 담습니다. 폐쇄망/대상 환경에서 아래를 직접 설치하세요:

```bash
pip install deepagents "langgraph-cli[inmem]" langchain-anthropic
#            └ 그래프      └ langgraph dev 서빙    └ 모델(원하면 다른 langchain 챗모델로 대체)
```

| 구분 | 패키지 | 비고 |
|---|---|---|
| 두뇌 | `deepagents` (→ langchain·langgraph) | langgraph 그래프 |
| 서빙 | `langgraph-cli[inmem]` | `langgraph dev` (LangGraph Server) |
| 모델 | `langchain-anthropic`(→`anthropic`, 기본) 또는 사내 LLM 용 챗모델 | ⚠️ anthropic 은 httpx(외부망) |
| 프론트(번들됨) | `@jupyterlab/application`·`ui-components`, `@lumino/widgets`·`messaging` | 빌드 시 |

> langgraph 는 deepagents 0.4.5 호환을 위해 1.0.8+ 필요(작업 시 1.2.2).

## 사용 예시

### 1) 빌드 (dev 환경, 외부 npm 접근 필요)
```bash
jlpm install
jlpm run build:prod        # TS → labextension 번들
pip wheel . -w dist --no-deps   # 반입/배포용 .whl (의존성 미포함)
```

### 2) 설치 & 실행 (jupyter 재시작 없이)
```bash
# (대상 env 에) 의존성 + 프론트 설치
pip install deepagents "langgraph-cli[inmem]" langchain-anthropic
pip install jlab_sidebar_chatbot-*.whl       # 또는 노트북: %pip install dist/*.whl
```
- **브라우저 새로고침** → 우측 💬 탭 등장.
- **LangGraph Server 시작** (이 폴더에서, `ANTHROPIC_API_KEY` 설정):
  ```bash
  langgraph dev --allow-blocking        # http://127.0.0.1:2024
  ```
  (노트북에서 `subprocess.Popen([...])` 로 띄워도 됩니다 — `demo.ipynb` ② 셀 참고.)
- 우측 💬 탭에서 대화 → LangGraph thread/run API → Claude(deepagents).

### 3) 모델·시스템 프롬프트 교체
`graph.py` 의 `make_graph()` 를 수정합니다(모델 `ChatAnthropic(...)`, `system_prompt`, `tools`). 사내 LLM 이면 `ChatAnthropic` 를 해당 langchain 챗모델로 교체. 수정 후 `langgraph dev` 재시작.

## 빌드 시 주의 (lockfile 없는 환경에서 필요한 3가지 핀)
1. **`.yarnrc.yml` `nodeLinker: node-modules`** — jlpm 기본 PnP 는 `@jupyterlab/builder`(webpack)와 비호환.
2. **`tsconfig.json` `skipLibCheck: true`** — 일부 의존성 `.d.ts` 가 최신 `@types/node` 와 충돌.
3. **`package.json` `resolutions.webpack = 5.88.2`** — builder 의 `license-webpack-plugin@2.x` 가 최신 webpack 에서 깨짐.

## 알려진 제약/한계점
- **온라인/개발 전용** — 기본 모델 Claude(외부망). 키(`ANTHROPIC_API_KEY`) 필수. 폐쇄망은 모델 교체 필요.
- **wheel 무의존성** — deepagents·langgraph-cli·모델 라이브러리를 직접 설치해야 동작.
- **localhost 전제** — 브라우저 `127.0.0.1` == `langgraph dev` 서버 기계. 원격 호스트명 접속 시 미동작.
- **LangGraph Server 를 따로 실행** — `langgraph dev` (포트 기본 2024, `handler.ts` 의 `LANGGRAPH_PORT` 와 일치).
- **single-file 아님** — 다수 파일 + npm 빌드. **JupyterLab 4 전용**. 스트리밍/마크다운 렌더는 범위 외.
