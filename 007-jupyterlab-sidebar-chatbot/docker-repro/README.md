# docker-repro — 폐쇄망 Pod(8888만 열린 JupyterLab) 재현 환경

실제 운영(JupyterHub Pod, ingress 로 8888만 노출)의 **네트워크 토폴로지**를 로컬에서
재현해, 사이드바 챗봇의 **커널-Comm 전송이 그 환경에서 동작함을 검증**하고(현재 전송 방식),
옛 HTTP 전송이 왜 거기서 실패했는지 이해하기 위한 개발/테스트용 컨테이너입니다.

> ⚠️ 변환물 본체가 아니라 **개발·재현용 도구**입니다(반입 대상 아님).

## 무엇을 재현하나

| 실제 환경 | 이 컨테이너 |
|---|---|
| Pod (커널이 도는 곳) | **컨테이너 내부** |
| 당신 노트북/PC 의 브라우저 | **호스트 브라우저** |
| ingress 가 8888만 노출 | compose 가 **8888만 publish** (8765 는 일부러 안 함) |
| `https://jupyterhub.example.com/user/<id>/<server>/lab` | `http://127.0.0.1:8888/user/<id>/<server>/lab` (base_url 흉내) |

핵심: 호스트 브라우저는 컨테이너의 `127.0.0.1` 에 닿지 못합니다(브라우저의 localhost ≠
컨테이너의 localhost). 그래서 **별도 포트(옛 8765)** 로 가던 전송은 실패하고, **커널
웹소켓(8888 경유, Hub 가 프록시)** 을 타는 Comm 전송은 됩니다.

## 띄우기

```bash
# 007 루트에서 (휠이 dist/ 에 있어야 함 — 없으면: uv build --wheel --out-dir dist)
docker compose -f docker-repro/docker-compose.yml up -d --build

# 열기 (토큰 demo)
#   http://127.0.0.1:8888/user/<id>/<server>/lab?token=demo

# 내리기
docker compose -f docker-repro/docker-compose.yml down
```

## 검증 — 커널-Comm 전송이 8888-only 에서 동작 (현재 기본 전송)

1. 위 URL 로 JupyterLab 접속(프론트를 새로 설치/빌드했다면 하드 새로고침 ⌘/Ctrl+Shift+R 한 번).
2. `demo.ipynb` 의 **② 셀(`register_chatbot_comm(...)`)** 실행 — 또는 모델 없이 전송만
   점검하려면 **(선택) 자가진단 셀**(`comm_selftest_cell.py`, 의존성 0) 실행.
3. 우측 **💬 Chatbot** 탭에서 메시지 전송 → **답변이 토큰 스트리밍으로** 옵니다.
   → 8888 만 열린 컨테이너에서 되면, 같은 토폴로지의 실제 Pod 에서도 됩니다.
   (통신은 `.../user/<id>/<server>/api/kernels/<id>/channels` 커널 웹소켓 — JupyterHub 가 프록시.)

> 호스트 Ollama 를 쓰는 경우(②의 `provider="ollama"`) 컨테이너 커널이
> `host.docker.internal:11434` 로 호스트 Ollama 를 호출합니다(아래 메모).

## (참고) 왜 옛 HTTP 전송은 여기서 실패했나

옛 전송(레거시 `server.py`)은 두뇌를 컨테이너 안 `127.0.0.1:8765` 로 띄웠습니다. 이 컨테이너에서:

- JupyterLab **Terminal**(컨테이너 내부)에서 `start_test_server()` 로 8765 를 띄우면
  `curl http://127.0.0.1:8765/health` → **200 OK** (서버는 컨테이너 안에 정상).
  ```python
  from jlab_sidebar_chatbot import start_test_server   # 레거시 HTTP 전송(점검용)
  start_test_server()
  ```
- 하지만 **호스트 브라우저**의 `127.0.0.1:8765` 는 *호스트* 의 localhost(아무것도 없음)
  → **ERR_CONNECTION_REFUSED**.

"터미널 curl 은 되는데 브라우저만 안 됨" — 이 역설이 옛 HTTP 가 원격/Pod 에서 깨진
이유이고, 그래서 전송을 커널-Comm 으로 바꿨습니다.

> ⚠️ **현재 프론트(💬 탭)는 8765 HTTP 를 쓰지 않습니다.** 위 curl 데모는 토폴로지를
> 설명하기 위한 것이며, 현재 💬 탭은 위 '검증' 섹션처럼 커널-Comm 으로 정상 동작합니다.

## 메모

- **호스트 Ollama 재사용**: 컨테이너 커널 → `host.docker.internal:11434` (compose 의
  `OLLAMA_BASE_URL`). 호스트에 `qwen3.5:0.8b` 가 있어야 함(`ollama list` 로 확인).
- **8765 는 publish 하지 않습니다** — 일부러(옛 HTTP 가 왜 실패하는지 보이려고). publish 하면 그 차이가 안 보입니다.
- 운영 이미지 자체가 아니라 **네트워크 토폴로지**를 재현합니다(OS/패키지 버전은 다를 수 있음).
- 007 폴더가 `/work` 로 마운트되어, 호스트에서 소스를 고치면 컨테이너에서도 보입니다
  (단 labextension 은 휠 재설치 + 브라우저 새로고침 필요).
