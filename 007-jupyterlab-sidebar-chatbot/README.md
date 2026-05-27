# 007 - JupyterLab Sidebar Chatbot

> **한 줄 요약**: JupyterLab **우측 사이드바**에 탭으로 뜨는 챗봇. 프론트엔드는 정통 TypeScript labextension, 두뇌는 **노트북 셀에서 띄우는 localhost HTTP 서버**(Mock LLM + 교체 어댑터, 표준 라이브러리만). **jupyter 서버 재시작이 필요 없도록** 설계 — 서버를 못 끄는 폐쇄망 환경 대응.

## ⚠️ 이 변환물은 single-file 이 아닙니다 (의도된 선택)

이 리포의 다른 변환물(001·005·006 …)은 "한 개의 `.py`" 를 핵심 가치로 삼지만, **JupyterLab 우측 사이드바 익스텐션은 구조상 TypeScript + npm 빌드 체인이 필요**합니다. 사용자가 "정통 TS labextension" 방식을 명시적으로 선택했기에 그대로 구현했습니다.

- 폐쇄망 반입 단위는 `.py` 가 아니라 **빌드된 wheel(`.whl`)** 입니다.
- **빌드 시점에만** 외부 npm 레지스트리 접근이 필요하고, 산출 wheel 에는 외부 참조가 없습니다.
- 코드 리뷰 표면이 넓어 보안 심사 부담이 큽니다. 가벼운 대안이 필요하면 **005(노트북 위젯)** 또는 **006(터미널 TUI)** 을 보세요.

## 아키텍처 — jupyter 서버 재시작 불필요

```
브라우저: JupyterLab 페이지 (localhost)
┌───────────────┬──────────────────┬─────────────────────┐
│  파일 탐색기   │   노트북/에디터    │  💬 챗봇 탭 (right)  │  ← 프론트 labextension (src/)
└──────┬────────┴──────────────────┴─────────┬───────────┘
       │ 셀에서 start_brain_server() 실행        │ fetch http://127.0.0.1:8765/chat
       ▼                                        ▼
   노트북 커널 (Python 프로세스 — 내가 셀을 실행할 수 있음)
   ┌────────────────────────────────────────────┐
   │ server.py  (stdlib http.server + CORS)       │
   │     └─ ChatBrain (멀티턴) → LLMAdapter        │
   │            ├─ MockLLM (기본)                  │
   │            └─ 사내 LLM (교체)                 │
   └────────────────────────────────────────────┘
```

**왜 이렇게?** 회사의 jupyter 서버를 **재시작할 수 없는** 환경 때문입니다.
- 프론트 labextension 은 설치 후 **브라우저 새로고침**만으로 뜹니다 — jupyter 가 페이지 로드마다 labextensions 폴더를 스캔하므로 **서버 재시작 불필요**.
- 두뇌를 'jupyter 서버 익스텐션'으로 두면 등록에 **서버 재시작이 필요**해 못 씁니다. 그래서 **노트북 셀에서 띄우는 localhost HTTP 서버**로 분리했습니다(커널은 내가 자유롭게 시작/재시작 가능).

> **전제 조건**: 브라우저의 `127.0.0.1` 과 커널의 `127.0.0.1` 이 **같은 기계**여야 합니다(로컬 실행 · 사내 컨테이너 · SSH 포트포워딩). 브라우저가 **원격 호스트명**으로 접속하면 이 방식은 커널의 localhost 에 닿지 못합니다 — 그 경우 프론트가 Jupyter 커널 웹소켓으로 직접 대화하는 방식이 필요합니다(미구현).

## 원본 출처

| 항목 | 값 |
|---|---|
| UI 패턴 | [jupyterlab/extension-examples · shout-button-message](https://github.com/jupyterlab/extension-examples/tree/main/shout-button-message) — `Widget` 상속 + `app.shell.add(widget, 'right')` + `onAfterAttach`/`onBeforeDetach` 라이프사이클을 그대로 차용 (단, 탭 내용은 "버튼"이 아니라 챗 UI) |
| 타겟 | JupyterLab **4.5.x** / jupyter_server **2.x** |
| 라이선스 | BSD-3-Clause (`LICENSE` 참조 — JupyterLab 생태계 관례) |
| 두뇌 패턴 | 본 리포 `001-langgraph-notebook-chatbot` 의 "Mock + 교체 어댑터 + 멀티턴" 개념 (단, langgraph 의존 없이 표준 라이브러리로 재구현) |

## 기능 요약

- **우측 사이드바 탭** — `app.shell.add(widget, 'right')`. shout 예제와 동일하게 우측에 아이콘 탭이 생기고, 그 안의 내용이 (버튼이 아니라) 챗 UI 입니다.
- **shout 예제 라이프사이클** — `ChatWidget` 은 생성자에서 DOM 을 만들고, 리스너는 `onAfterAttach` 에서 등록 → `onBeforeDetach` 에서 해제합니다.
- **채팅 UI** — 말풍선 목록 · 입력창(Enter 전송 / Shift+Enter 줄바꿈) · `새 대화`(reset) 버튼. JupyterLab CSS 변수를 사용해 라이트/다크 테마에 자동 적응.
- **멀티턴 메모리** — `session_id` 별 대화 맥락 유지(`ChatBrain`).
- **Mock LLM 기본 탑재** — 모델 weight·인터넷 없이 전체 흐름 즉시 체험.
- **어댑터 한 줄 교체** — `LLMAdapter.generate(messages)` 하나만 구현하면 사내 LLM 으로 전환.
- **영속화 없음** — 대화는 서버 메모리에만 존재(바이너리 파일 저장 금지 원칙 준수).

## 의존성

| 구분 | 패키지 | 용도 |
|---|---|---|
| 런타임(두뇌·셀 서버) | **없음** — `http.server` 등 표준 라이브러리만 | 챗봇 두뇌 + localhost HTTP 서버 |
| 런타임(프론트, 번들됨) | `@jupyterlab/application`·`ui-components`, `@lumino/widgets`·`@lumino/messaging` | 사이드바 Widget·라이프사이클 |
| 빌드 전용 | `@jupyterlab/builder`, `typescript`, `rimraf`, node/npm | TS→번들 |

> 두뇌(`jlab_sidebar_chatbot/`)는 **표준 라이브러리만** 사용합니다(`jupyter_server` 도 불필요). numpy/torch/langgraph 등은 쓰지 않습니다.

## 사용 예시

### 1) 두뇌만 빠르게 확인 (빌드 불필요)

```bash
python basic_usage.py        # 멀티턴·세션격리·reset·어댑터교체 데모
```

`demo.ipynb` 에서도 두뇌를 인라인으로 돌려볼 수 있습니다.

### 2) 빌드 (dev 환경, 외부 npm 접근 필요)

```bash
jlpm install
jlpm run build:prod      # TS → labextension 번들
pip wheel . -w dist      # 폐쇄망 반입용 .whl 생성
```

### 3) 설치 & 사용 — **jupyter 서버 재시작 없이**

폐쇄망(이미 jupyter 가 떠 있고 내가 끌 수 없는 환경)에서. **터미널을 못 여는 환경이면 1) 을 노트북 셀에서 하면 됩니다.**

**1) 프론트 labextension 설치** — 아래 둘 중 하나 (둘 다 jupyter 가 쓰는 env 에 설치):

```bash
# (a) 터미널에서
pip install jlab_sidebar_chatbot-*.whl      # 또는 dev: pip install -e .
```

```python
# (b) 터미널 없이, 노트북 셀에서 — %pip 매직이 현재 커널/서버 env 에 설치
%pip install jlab_sidebar_chatbot-0.1.0-py3-none-any.whl
```

**2) 브라우저에서 JupyterLab 페이지 새로고침** → 우측에 💬 탭 등장 (서버 재시작 X).

그다음 **노트북 셀에서 두뇌 서버를 한 줄로** 띄웁니다(커널은 내가 자유롭게 실행 가능):

```python
from jlab_sidebar_chatbot import start_brain_server
start_brain_server()        # http://127.0.0.1:8765 (백그라운드 스레드)
```

이제 우측 💬 탭에서 대화하면 됩니다. (셀을 실행하기 전이면 탭이 "셀에서 먼저 실행하세요" 안내를 보여줍니다.)

> 검증 결과: 같은 실행 중인 jupyter 서버에 labextension 을 설치만 했을 때, **새로고침으로 프론트 탭은 떴고**(static 200, federated 등록 1), 서버 익스텐션 방식의 백엔드는 재시작 전엔 404 였습니다. 그래서 두뇌를 셀 서버로 분리했습니다.

### 4) 사내 LLM 으로 교체

`LLMAdapter` 를 하나 구현해 `start_brain_server(brain=...)` 로 주입합니다.

```python
from jlab_sidebar_chatbot import start_brain_server, ChatBrain
from jlab_sidebar_chatbot.llm import LLMAdapter

class MyLocalLLM(LLMAdapter):
    def generate(self, messages):
        # messages: [{"role": "user"/"assistant"/"system", "content": ...}, ...]
        # → 사내 모델/로컬 추론 서버 호출 후 답변 문자열만 반환
        ...

start_brain_server(brain=ChatBrain(adapter=MyLocalLLM()))
```

> 로컬 추론 서버를 HTTP 로 호출해야 한다면 `requests`/`httpx` 는 차단 패키지이므로 표준 `urllib.request` 를 사용하세요.

## 빌드 시 주의 (lockfile 없는 환경에서 필요한 3가지 핀)

이 폴더는 `yarn.lock` 을 커밋하지 않으므로(반입 단위는 wheel), 신선한 환경에서 빌드할 때 최신 전이 의존이 잡혀 깨질 수 있습니다. 그래서 다음 3가지를 고정해 두었습니다.

1. **`.yarnrc.yml` 의 `nodeLinker: node-modules`** — jlpm 기본값인 Yarn PnP(`.pnp.cjs`)는 `@jupyterlab/builder`(webpack)와 호환되지 않습니다.
2. **`tsconfig.json` 의 `skipLibCheck: true`** — 일부 의존성(`lib0`, `@jupyterlab/coreutils`)의 `.d.ts` 가 최신 `@types/node` 와 충돌(`Uint8Array is not generic` 등). 내 소스는 그대로 타입체크되고 node_modules 선언만 건너뜁니다.
3. **`package.json` 의 `resolutions.webpack = 5.88.2`** — `@jupyterlab/builder@4.5.7` 이 쓰는 `license-webpack-plugin@2.x` 가 최신 webpack(5.107+)의 모듈 형상에서 `getActualFilename ... 'trim'` 에러로 깨집니다. builder 하한(`^5.76.1`) 위이면서 플러그인 호환이 확실한 버전으로 고정.

> 검증 완료(2026-05-27): `jlpm run build:prod` → `webpack 5.88.2 compiled successfully`, `jlab_sidebar_chatbot/labextension/static/` 에 `remoteEntry.*.js`·`style.js`·`third-party-licenses.json` 생성.

## 알려진 제약/한계점

- **localhost 전제** — 브라우저의 `127.0.0.1` 과 커널이 같은 기계여야 합니다. 브라우저가 원격 호스트명으로 접속하는 환경이면 이 방식은 동작하지 않습니다(프론트 ↔ 커널 웹소켓 직접 통신 방식이 필요 — 미구현).
- **셀 한 줄 실행 필요** — 매 커널 세션마다 `start_brain_server()` 를 한 번 실행해야 두뇌가 붙습니다(자동 시작 아님). 포트 기본값 8765 (`server.py` / `handler.ts` 양쪽 상수 일치 필요).
- **single-file 아님** — 다수 파일 + npm 빌드 필요(위 경고 참고).
- **Mock LLM 은 규칙 기반 에코** — 실제 추론 안 함. 운영 전 어댑터 연결 필수.
- **대화 기록은 메모리 한정** — 커널 재시작 / `stop_brain_server()` 시 소실. 보존이 필요하면 self-contained HTML 내보내기를 별도 구현.
- **JupyterLab 4 전용** — 3.x 미지원.
- 스트리밍 응답·마크다운 렌더·코드 하이라이트는 범위에서 제외(최소 골격). 필요 시 확장하세요.
