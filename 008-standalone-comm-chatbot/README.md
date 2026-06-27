# 008 · Standalone Comm Chatbot

> **JupyterLab/사이드바 없이, 브라우저로 `chat.html` 하나만 열어 원격 Jupyter 커널의 두뇌(007)에 붙는 독립 챗봇.**

007(jlab-sidebar-chatbot)의 두뇌는 **커널 안**에서 돌고, 사이드바는 **Jupyter Comm(커널 웹소켓)** 으로 그 두뇌와 대화합니다. 008 은 사이드바 자리를 대신하는 **독립 클라이언트**입니다. JupyterLab 확장을 설치하지 않아도, 그냥 이 HTML 한 장을 브라우저로 열면 같은 챗봇을 씁니다.

이 문서의 출발점이 된 질문:
> "comm 방식으로 jupyter kernel 과 통신하는 게 있다면, 사이드바가 아니어도 챗봇을 하나 만들어 local 에서 띄워도 되겠네?"

→ **됩니다.** comm 의 반대쪽 끝은 "Jupyter 프로토콜을 말할 수 있는 무엇이든" 이면 되고, 008 이 바로 그 "무엇"입니다.

---

## 원본 출처

| 항목 | 내용 |
|---|---|
| 짝 커널 패키지 | **007 jlab-sidebar-chatbot** (`jlab_sidebar_chatbot`, `register_chatbot_comm`) |
| 프로토콜 스펙 | [Jupyter 메시징](https://jupyter-client.readthedocs.io/en/stable/messaging.html), Jupyter Server `api/kernels` REST + 커널 WebSocket 채널 |
| 재구현한 것 | 007 의 `kernelClient.ts`(@jupyterlab/services 의존)를 **라이브러리 없이** 브라우저 표준 API 로 재현 |
| 라이선스 | BSD-3-Clause (007 과 동일, 자체 작성) |

---

## 기능 요약

- **single-file HTML** — 외부 CDN·빌드 도구·패키지 0. `fetch` / `WebSocket` / `crypto.randomUUID` 만 사용 (폐쇄망 반입에 유리, self-contained).
- **자동 등록** — 사용자가 노트북 셀에서 `register_chatbot_comm()` 을 직접 칠 필요 없음. 클라이언트가 커널에 `execute_request` 로 알아서 실행.
- **007 과 동일한 comm 프로토콜** — `{message|reset}` 보내고 `{ready|token|done|error|reset_ok}` 받음. 토큰 스트리밍 + 중간 단계 접이식 표시.
- **모델 제공자 선택** — `ollama`(키 불필요) / `openai`(사내 vLLM `base_url`·`api_key` 입력) / `stub`(모델 호출 0, 전송 경로만 점검).
- **원격/JupyterHub 대응** — `base_url`(예: `/user/<id>/<server>/`)까지 포함한 주소 정규화, `http→ws` 스킴 변환, 토큰 인증.

---

## 통신 경로

```
이 브라우저 (chat.html)
   │  ① POST {base}api/kernels            (커널 생성)        헤더 Authorization: token <t>
   │  ② WS   {base}api/kernels/<id>/channels?token=<t>      (커널 메시징 채널)
   ▼
Jupyter Server ──(프록시: JupyterHub base_url)──▶ 커널(Pod)
                                                    │  ③ execute_request → register_chatbot_comm()
                                                    │  ④ comm_open(target='jlab_sidebar_chatbot')
                                                    │  ⑤ comm_msg {type:'message', ...}
                                                    ▼
                                                 사내 LLM (Pod 안에서만 망 접근)
```

망 경계를 넘는 것은 **Jupyter 프로토콜뿐** — 두뇌·LLM 호출은 전부 커널(Pod) 안에서 일어나므로 폐쇄망 제약과 맞습니다. (007 이 HTTP→Comm 으로 전환한 이유와 동일.)

---

## 사용법

### 1) 대상 커널에 007 패키지가 있어야 함
원격 Jupyter 의 커널에서 `import jlab_sidebar_chatbot` 이 되어야 합니다 (007 의 wheel 설치). `stub` 제공자를 골라도 `jlab_sidebar_chatbot` import 는 필요합니다(모델만 안 부름).

### 2) `chat.html` 열기 — 두 가지 방법

**(A) 같은 출처로 열기 — CORS 설정 불필요 (권장)**
이 파일을 그 Jupyter 서버의 파일 트리에 두고 `/files/` 로 엽니다. 페이지 출처 = API 출처라 CORS 가 아예 없습니다.
```
http://<host>/user/<id>/<server>/files/chat.html?token=demo
```

**(B) 다른 출처로 열기 — 서버에 CORS 허용 필요**
`file://` 로 직접 열거나 다른 포트에서 서빙하면, 원격 Jupyter 가 아래처럼 떠 있어야 합니다 (토큰 인증이라 쿠키/credentials 는 불필요):
```
jupyter lab --ServerApp.allow_origin='*'
```
- 제약의 핵심은 **WebSocket** 입니다. REST 는 토큰 인증 시 출처를 봐주는 우회가 있지만, 커널 채널 WS 의 `check_origin` 에는 그 우회가 없어 `allow_origin` 설정이 반드시 필요합니다.
- `file://` 페이지는 브라우저가 `Origin: null` 을 보내므로 **정확히 `'*'`** 여야 통과합니다(특정 출처 문자열로 좁히면 `null` 과 안 맞아 실패).
- **JupyterHub** 는 토큰만으로 `_xsrf` 가 면제되지 않을 수 있습니다(jupyterhub#4845). 이 클라이언트는 **같은 출처**일 때 `_xsrf` 쿠키를 읽어 헤더로 동봉해 대응하므로, JupyterHub 에서는 위 (A) 같은-출처 방식을 권장합니다(교차 출처는 JS 가 `_xsrf` 쿠키를 못 읽어 미지원).

### 3) 연결 패널 입력
- **Jupyter 주소**: `http://127.0.0.1:8888/user/<id>/<server>/` (끝에 `lab?token=…` 이 붙어 있어도 자동 정리)
- **토큰**: 서버 실행 시의 token (예: `demo`)
- **기존 커널 ID**: 비우면 새 커널 생성, 채우면 그 커널에 붙음
- **모델 제공자/모델명**: `ollama` + `qwen3.5:0.8b` 등

**연결** → `연결됨` 이 뜨면 대화 시작.

---

## 사용 예시

별도 예제 스크립트가 없습니다 — **`chat.html` 자체가 예제이자 산출물**입니다(노트북 변환물의 `demo.ipynb` 와 같은 위치). 브라우저로 열어 연결 패널을 채우면 됩니다.

가장 빠른 점검(007 의 docker-repro 사용):
```bash
# 007 폴더에서 (8888 publish + base_url /user/<id>/<server>/ + token demo)
docker compose -f docker-repro/docker-compose.yml up -d --build
```
- 같은 출처로 쓰려면: `chat.html` 을 마운트된 작업폴더에 두고 `http://127.0.0.1:8888/user/<id>/<server>/files/chat.html?token=demo`
- 제공자 `stub` 으로 먼저 **전송 경로만**(모델 없이 `ready→done`) 확인 → 그다음 `ollama` 로 실제 답변.

---

## 알려진 제약

- **007 패키지 의존**: 커널에 `jlab_sidebar_chatbot` 이 없으면 등록 단계에서 `ImportError` 를 화면에 표시.
- **레거시 WS 프로토콜 가정**: 서브프로토콜을 요청하지 않아 jupyter_server 가 **JSON 텍스트 프레임**으로 동작하는 경로에 의존. 이 fallback 이 제거된 미래 버전에선 바이너리 `v1` 프레이밍 구현이 추가로 필요(코드에 `ponytail:` 주석으로 표시).
- **CORS**: 다른 출처에서 열면 `allow_origin` 필요(위 참고).
- **새 커널 누적**: 기존 커널 ID 를 비우면 연결마다 새 커널 생성. `연결 해제`는 WS 만 닫고 커널은 남김(정리는 Jupyter 측에서).
- **online_only**: 전송(Comm)은 망 독립이나 실제 답변은 모델 호출 필요.
- **보안**: 입력한 토큰은 그 서버에서 임의 코드 실행 권한과 같음. 신뢰하는 서버에만, 공용 PC 면 사용 후 탭 닫기. 토큰은 디스크에 저장하지 않고 메모리에만 보관.

---

## 007 과의 관계

| | 007 사이드바 | 008 standalone |
|---|---|---|
| 형태 | JupyterLab 확장(다파일 + npm 빌드, wheel 반입) | single-file HTML |
| 붙는 커널 | 활성 노트북의 커널 | 새 커널 생성 또는 지정 커널 |
| register | 사용자가 셀에서 직접 | 클라이언트가 자동 실행 |
| 전송 라이브러리 | `@jupyterlab/services` | 없음 (raw fetch/WebSocket) |
| 두뇌·프로토콜 | **동일** (`jlab_sidebar_chatbot` comm target) | **동일** |
