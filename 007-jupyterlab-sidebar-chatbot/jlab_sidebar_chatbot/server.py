"""
노트북 커널에서 띄우는 챗봇 백엔드 (localhost HTTP 서버)

배경: 회사 jupyter 서버를 재시작할 수 없는 환경을 위한 설계입니다.
챗봇 두뇌를 'jupyter 서버 익스텐션'(등록에 서버 재시작 필요)이 아니라,
'노트북 셀에서 시작하는 작은 localhost HTTP 서버'로 제공합니다. 우측 사이드바
프론트엔드가 이 서버에 fetch 합니다. 표준 라이브러리만 사용합니다.

사용 (노트북 셀 한 줄):
    from jlab_sidebar_chatbot import start_brain_server
    start_brain_server()              # http://127.0.0.1:8765 (백그라운드 스레드)

실제 사내 LLM 으로 교체:
    from jlab_sidebar_chatbot import start_brain_server, ChatBrain
    start_brain_server(brain=ChatBrain(adapter=MyLocalLLM()))

전제 조건: 브라우저의 127.0.0.1 과 커널의 127.0.0.1 이 같은 기계여야 합니다
(로컬 실행 / 사내 컨테이너 / SSH 포트포워딩 환경). 브라우저가 원격 호스트명으로
접속하는 환경이면 이 방식은 브라우저에서 커널의 localhost 에 닿지 못합니다.
"""

from __future__ import annotations

import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Optional

from .llm import ChatBrain

# 프론트엔드(handler.ts)의 DEFAULT_PORT 와 반드시 일치해야 합니다.
DEFAULT_PORT = 8765

# 포트별로 떠 있는 서버를 기억해 중복 기동을 막습니다.
_servers: Dict[int, ThreadingHTTPServer] = {}


def _make_handler(brain: ChatBrain):
    """주어진 두뇌(brain)에 묶인 요청 핸들러 클래스를 만듭니다."""

    class _Handler(BaseHTTPRequestHandler):
        # ── CORS: 프론트(=jupyter 페이지)와 출처가 달라 브라우저가 CORS 를 요구 ──
        def _cors(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")

        def _json(self, code: int, payload: dict):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self._cors()
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):  # 프리플라이트
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_GET(self):
            path = self.path.split("?")[0].rstrip("/")
            if path.endswith("/health") or path in ("", "/"):
                self._json(200, {"ok": True, "service": "jlab-sidebar-chatbot"})
            else:
                self._json(404, {"error": "unknown path"})

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0) or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._json(400, {"error": "잘못된 JSON"})
                return

            path = self.path.split("?")[0].rstrip("/")
            session_id = (body.get("session_id") or "default") or "default"

            if path.endswith("/chat"):
                message = (body.get("message") or "").strip()
                if not message:
                    self._json(400, {"error": "message 가 비어 있습니다"})
                    return
                self._json(200, brain.send(session_id, message))
            elif path.endswith("/reset"):
                brain.reset(session_id)
                self._json(200, {"ok": True})
            else:
                self._json(404, {"error": "unknown path"})

        def log_message(self, *args):  # 노트북 출력 조용히
            pass

    return _Handler


def _is_our_server(host: str, port: int) -> bool:
    """해당 포트에 '이미 우리 챗봇 서버'가 떠 있으면 True (다른 커널/프로세스 포함)."""
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/health", timeout=0.5) as resp:
            return json.loads(resp.read()).get("service") == "jlab-sidebar-chatbot"
    except Exception:
        # 연결 거부(아무도 없음) 또는 다른 서비스 → 우리 것이 아님
        return False


def start_brain_server(
    port: int = DEFAULT_PORT,
    brain: Optional[ChatBrain] = None,
    host: str = "127.0.0.1",
) -> Optional[ThreadingHTTPServer]:
    """노트북 셀에서 호출 — 백그라운드 스레드로 챗봇 서버를 띄웁니다.

    셀은 즉시 반환되며 서버는 커널이 살아있는 동안 계속 동작합니다.
    포트 충돌([Errno 48] Address already in use)을 우아하게 처리합니다:
      - 같은 커널에서 이미 띄웠으면 그 서버를 재사용
      - 다른 커널/프로세스가 '같은 챗봇 서버'를 띄웠으면 그대로 사용(프론트는 이미 동작)
      - 그 외 프로세스가 포트를 점유했으면 명확한 안내 후 None 반환(트레이스백 없이)
    """
    if port in _servers:
        print(f"이미 이 커널에서 실행 중입니다: http://{host}:{port}")
        return _servers[port]

    # 다른 커널/프로세스가 이미 같은 챗봇 서버를 띄워 둔 경우 → 그대로 사용
    if _is_our_server(host, port):
        print(
            f"이미 챗봇 서버가 http://{host}:{port} 에 떠 있습니다(다른 커널/프로세스). "
            "그대로 우측 💬 탭을 쓰면 됩니다."
        )
        return None

    brain = brain or ChatBrain(system_prompt="너는 노트북 커널에서 도는 도우미야.")
    try:
        httpd = ThreadingHTTPServer((host, port), _make_handler(brain))
    except OSError as exc:
        # 우리 서버가 아닌 다른 프로세스가 포트를 점유 중
        print(
            f"⚠️ 포트 {port} 를 열 수 없습니다: {exc}\n"
            f"   다른 프로세스가 {port} 를 사용 중입니다.\n"
            f"   ① 이전에 띄운 게 남아 있으면  stop_brain_server({port})  후 다시 시도\n"
            f"   ② 커널을 막 재시작했다면 잠시 후(소켓 정리) 다시 시도\n"
            f"   ③ 다른 포트를 쓰려면  start_brain_server(port=8766)  — 단 프론트의\n"
            f"      handler.ts DEFAULT_PORT 도 같은 값으로 맞춰 다시 빌드해야 합니다."
        )
        return None

    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    _servers[port] = httpd
    print(f"✅ 챗봇 서버 시작: http://{host}:{port}  — 우측 💬 탭에서 대화하세요.")
    return httpd


def stop_brain_server(port: int = DEFAULT_PORT) -> None:
    """띄운 서버를 중지합니다."""
    httpd = _servers.pop(port, None)
    if httpd is not None:
        httpd.shutdown()
        print(f"중지됨: 포트 {port}")
    else:
        print(f"실행 중인 서버가 없습니다: 포트 {port}")


# ===== Example Usage =====
if __name__ == "__main__":
    # 직접 실행하면(노트북 밖) 포그라운드로 띄워 Ctrl+C 까지 동작합니다.
    httpd = ThreadingHTTPServer(
        ("127.0.0.1", DEFAULT_PORT),
        _make_handler(ChatBrain(system_prompt="너는 데모 도우미야.")),
    )
    print(f"http://127.0.0.1:{DEFAULT_PORT} 에서 동작 중 (Ctrl+C 로 종료)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.")
