# 007 - JupyterLab Sidebar Chatbot

> **한 줄 요약**: JupyterLab **우측 사이드바**에 탭으로 뜨는 챗봇. 프론트엔드는 정통 TypeScript labextension, 두뇌는 **langgraph 그래프(deepagents 로 생성) + Claude**. 직접 만든 LLM 추상화 클래스는 없고, 멀티턴은 langgraph 체크포인터(`thread_id`)가 관리합니다.

## ⚠️ 두 가지 중요한 성격

**1) 온라인/개발 전용 — 폐쇄망 배포용이 아닙니다.**
두뇌가 **Claude API(api.anthropic.com) 외부 호출** + `anthropic`/`langchain-anthropic`(httpx 기반, 폐쇄망 차단 패키지)을 씁니다. 즉 이 변환물은 **로컬 개발/데모** 성격이며, 리포의 폐쇄망 원칙(외부망 금지·로컬 LLM)과 정면으로 다릅니다. 폐쇄망에서 쓰려면 `build_chat_graph` 의 모델을 사내 LLM(예: 로컬 OpenAI 호환 엔드포인트용 langchain 챗모델)로 바꿔야 합니다.
API 키는 **환경변수 `ANTHROPIC_API_KEY` 로만** 읽습니다(코드/노트북에 하드코딩 금지).

**2) single-file 이 아닙니다.**
JupyterLab 우측 사이드바 익스텐션은 구조상 TypeScript + npm 빌드가 필요합니다. 반입 단위는 `.py` 가 아니라 빌드된 wheel(`.whl`)이고, 코드 리뷰 표면이 넓습니다. 가벼운 대안은 **005(노트북 위젯)** · **006(터미널 TUI)** 참고.

## 아키텍처

```
브라우저: JupyterLab 페이지 (localhost)
┌───────────────┬──────────────────┬─────────────────────┐
│  파일 탐색기   │   노트북/에디터    │  💬 챗봇 탭 (right)  │  ← 프론트 labextension (src/)
└──────┬────────┴──────────────────┴─────────┬───────────┘
       │ 셀에서 start_graph_server() 실행       │ fetch http://127.0.0.1:8765/chat
       ▼                                        ▼
   노트북 커널 (Python 프로세스 — 내가 셀을 실행할 수 있음)
   ┌──────────────────────────────────────────────┐
   │ server.py  (stdlib http.server + CORS, 얕은 전송) │
   │     └─ graph.reply(graph, thread_id, message)   │
   │            └─ langgraph CompiledStateGraph       │
   │                 (deepagents.create_deep_agent)   │
   │                 + InMemorySaver (thread_id=세션)  │
   │                      └─ ChatAnthropic (Claude)   │
   └──────────────────────────────────────────────┘
```

- **프론트(`src/`)**: 우측 사이드바 Widget. 입력 시 `127.0.0.1:8765` 로만 fetch.
- **두뇌(`jlab_sidebar_chatbot/graph.py`)**: deepagents 가 만든 langgraph 그래프 하나. 커스텀 Adapter/Mock/Brain 없음.
- **서빙(`server.py`)**: 그래프를 감싸는 얕은 HTTP 전송 계층(`/chat`·`/reset`·`/health`).
- **멀티턴**: langgraph `InMemorySaver` + `thread_id`(=세션). 매 턴 새 메시지만 보내면 그래프가 기록을 복원. `/reset` 은 thread 를 새로 분기.

### jupyter 서버 재시작 불필요
- 프론트 labextension 은 설치 + **브라우저 새로고침**만으로 뜹니다(jupyter 가 페이지 로드마다 labextensions 폴더를 스캔).
- 두뇌는 'jupyter 서버 익스텐션'이 아니라 **노트북 셀에서 띄우는 localhost 서버**라, 서버 재시작이 필요 없습니다(커널은 내가 시작/재시작 가능).
- **전제**: 브라우저의 `127.0.0.1` 과 커널의 `127.0.0.1` 이 **같은 기계**여야 합니다(로컬·컨테이너·SSH 포트포워딩). 원격 호스트명 접속이면 닿지 않습니다(커널 웹소켓 직접 통신 필요 — 미구현).

## 원본 출처

| 항목 | 값 |
|---|---|
| UI 패턴 | [jupyterlab/extension-examples · shout-button-message](https://github.com/jupyterlab/extension-examples/tree/main/shout-button-message) — `Widget` 상속 + `app.shell.add(widget, 'right')` + `onAfterAttach`/`onBeforeDetach` 라이프사이클 차용 (탭 내용은 버튼이 아니라 챗 UI) |
| 두뇌 | [deepagents](https://github.com/langchain-ai/deepagents) 0.4.5 (`create_deep_agent`) → langgraph `CompiledStateGraph` + `InMemorySaver`, 모델 `langchain-anthropic` `ChatAnthropic`(Claude) |
| 타겟 | JupyterLab **4.5.x** |
| 라이선스 | BSD-3-Clause (`LICENSE` 참조 — JupyterLab 생태계 관례) |

## 기능 요약

- **우측 사이드바 탭** — `app.shell.add(widget, 'right')` (shout 예제 패턴). 탭 안은 챗 UI.
- **langgraph 두뇌** — deepagents 그래프 하나가 두뇌. 직접 만든 LLM 클래스 없음.
- **멀티턴** — langgraph `InMemorySaver` + `thread_id`(=세션). `/reset` 은 thread 분기로 초기화.
- **채팅 UI** — 말풍선 · 입력창(Enter 전송 / Shift+Enter 줄바꿈) · `새 대화`(reset). JupyterLab CSS 변수로 라이트/다크 자동 적응.
- **모델·프롬프트 교체** — `build_chat_graph(model=..., system_prompt=..., tools=...)` 로 그래프를 만들어 `start_graph_server(graph=...)` 주입.
- **영속화 없음** — 대화는 메모리(InMemorySaver)에만. 커널 재시작 시 소실.

## 의존성

| 구분 | 패키지 | 용도 |
|---|---|---|
| 런타임(두뇌) | `deepagents`·`langgraph`·`langchain`·`langchain-anthropic`(→`anthropic`) | langgraph 그래프 + Claude. ⚠️ `anthropic` 은 httpx 기반(외부망) — 폐쇄망 차단 |
| 런타임(서빙) | 표준 라이브러리 `http.server` | 얕은 HTTP 전송 |
| 런타임(프론트, 번들됨) | `@jupyterlab/application`·`ui-components`, `@lumino/widgets`·`@lumino/messaging` | 사이드바 Widget·라이프사이클 |
| 빌드 전용 | `@jupyterlab/builder`, `typescript`, `rimraf`, node/npm | TS→번들 |

> ⚠️ langgraph 는 deepagents 0.4.5 와의 호환을 위해 1.0.8+ 필요(이 작업에서 1.2.2 사용). 리포 기본 스택 핀(langgraph 1.0.10)과 다르므로 공유 `.venv` 에서 다른 변환물과 함께 쓸 때 주의.

## 사용 예시

### 1) 두뇌(그래프)만 빠르게 확인 — 빌드 불필요 (키 필요)

```bash
ANTHROPIC_API_KEY=sk-... python basic_usage.py     # langgraph 멀티턴(thread_id) 데모
```

`demo.ipynb` 의 ③ 셀에서도 그래프를 직접 호출해 볼 수 있습니다.

### 2) 빌드 (dev 환경, 외부 npm 접근 필요)

```bash
jlpm install
jlpm run build:prod      # TS → labextension 번들
pip wheel . -w dist      # 반입/배포용 .whl 생성
```

### 3) 설치 & 사용 — jupyter 서버 재시작 없이

**1) 프론트 labextension 설치** (jupyter 가 쓰는 env 에) — 터미널 또는 노트북 셀:

```bash
pip install jlab_sidebar_chatbot-*.whl       # 터미널. dev: pip install -e .
```
```python
%pip install dist/jlab_sidebar_chatbot-0.1.0-py3-none-any.whl   # 노트북 셀 (%pip)
```

**2) 브라우저 새로고침** → 우측 💬 탭 등장.

**3) 두뇌 서버 시작** (노트북 셀, `ANTHROPIC_API_KEY` 는 jupyter 서버 env 로 주입해 커널이 상속):

```python
from jlab_sidebar_chatbot import start_graph_server
start_graph_server()        # langgraph 그래프 서빙, http://127.0.0.1:8765
```

이제 우측 💬 탭에서 Claude(deepagents)와 대화합니다. 끝나면 `demo.ipynb` 의 ⑤ 셀로 정리(서버 중지 + `pip uninstall`).

### 4) 모델·시스템 프롬프트 교체

```python
from jlab_sidebar_chatbot import build_chat_graph, start_graph_server

graph = build_chat_graph(
    model="claude-sonnet-4-6",              # 또는 ANTHROPIC_MODEL 환경변수
    system_prompt="너는 SQL 전문가야.",
)
start_graph_server(graph=graph)
```

> 사내(폐쇄망) LLM 으로 바꾸려면 `graph.py` 에서 `ChatAnthropic` 대신 사내 엔드포인트용 langchain 챗모델을 써서 그래프를 만드세요.

## 빌드 시 주의 (lockfile 없는 환경에서 필요한 3가지 핀)

`yarn.lock` 을 커밋하지 않으므로, 신선한 환경 빌드 시 최신 전이 의존이 잡혀 깨질 수 있어 다음을 고정했습니다.

1. **`.yarnrc.yml` `nodeLinker: node-modules`** — jlpm 기본 PnP 는 `@jupyterlab/builder`(webpack)와 비호환.
2. **`tsconfig.json` `skipLibCheck: true`** — 일부 의존성 `.d.ts` 가 최신 `@types/node` 와 충돌. 내 소스는 그대로 검사.
3. **`package.json` `resolutions.webpack = 5.88.2`** — `@jupyterlab/builder@4.5.7` 의 `license-webpack-plugin@2.x` 가 최신 webpack 에서 `getActualFilename ... 'trim'` 에러. builder 하한(^5.76.1) 위 호환 버전 고정.

## 알려진 제약/한계점

- **온라인/개발 전용** — Claude API(외부망) + `anthropic`/`langchain-anthropic`(httpx). 폐쇄망 배포용 아님. 키(`ANTHROPIC_API_KEY`) 필수.
- **localhost 전제** — 브라우저 `127.0.0.1` == 커널 기계. 원격 호스트명 접속 시 미동작(커널 웹소켓 직통 필요, 미구현).
- **셀 한 줄 실행 필요** — 매 커널 세션마다 `start_graph_server()`. 포트 기본 8765 (`server.py`/`handler.ts` 상수 일치).
- **single-file 아님** — 다수 파일 + npm 빌드.
- **대화 기록은 메모리 한정** — 커널 재시작/`stop_graph_server()`/`/reset` 시 소실.
- **JupyterLab 4 전용**. 스트리밍 응답·마크다운 렌더는 범위 외(최소 골격).
