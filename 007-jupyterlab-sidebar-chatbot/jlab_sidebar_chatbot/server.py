"""
langgraph 그래프를 localhost HTTP 로 서빙하는 얕은 전송 계층.

두뇌 로직은 graph.py 의 langgraph 그래프(deepagents + 체크포인터)가 전담합니다.
이 파일은 노트북 셀에서 띄우는 작은 stdlib http.server 로, 우측 사이드바 프론트가
호출하는 /chat·/reset·/health 만 제공합니다(직접 만든 LLM 클래스는 없습니다).

엔드포인트:
    POST  /chat    {session_id, message} -> {role, answer, steps}  (steps=도구 단계, 접이식)
    POST  /reset   {session_id}          -> {ok: true}   (해당 세션 thread 를 새로 분기)
    GET   /health                         -> {ok: true}

⚠️ 온라인/개발 전용 (graph.py 가 Claude API 사용). 브라우저 127.0.0.1 == 커널 127.0.0.1 전제.
사용 (노트북 셀):
    from jlab_sidebar_chatbot import start_graph_server
    start_graph_server()      # ANTHROPIC_API_KEY env 필요 (셀에 키 적지 마세요)
"""

from __future__ import annotations

import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Optional

from .graph import build_chat_graph, run_turn

# 프론트엔드(handler.ts)의 DEFAULT_PORT 와 반드시 일치해야 합니다.
DEFAULT_PORT = 8765

_servers: Dict[int, ThreadingHTTPServer] = {}


def _make_handler(graph):
    """주어진 langgraph 그래프에 묶인 요청 핸들러 클래스를 만듭니다."""

    # 세션별 '대화 세대' — /reset 시 thread_id 를 새로 분기해 langgraph 기록을 비웁니다.
    reset_gen: Dict[str, int] = {}

    def thread_id(session_id: str) -> str:
        return f"{session_id}#{reset_gen.get(session_id, 0)}"

    class _Handler(BaseHTTPRequestHandler):
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
                try:
                    out = run_turn(graph, thread_id(session_id), message)
                except Exception as exc:  # 그래프/LLM 호출 실패를 클라이언트에 전달
                    self._json(500, {"error": f"그래프 호출 실패: {exc}"})
                    return
                # answer = 최종 답변(마크다운), steps = 접어서 보여줄 도구 단계들
                self._json(200, {"role": "assistant", **out})
            elif path.endswith("/reset"):
                reset_gen[session_id] = reset_gen.get(session_id, 0) + 1
                self._json(200, {"ok": True})
            else:
                self._json(404, {"error": "unknown path"})

        def log_message(self, *args):  # 노트북 출력 조용히
            pass

    return _Handler


def _is_our_server(host: str, port: int) -> bool:
    """해당 포트에 '이미 우리 챗봇 서버'가 떠 있으면 True."""
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/health", timeout=0.5) as resp:
            return json.loads(resp.read()).get("service") == "jlab-sidebar-chatbot"
    except Exception:
        return False


def start_graph_server(
    port: int = DEFAULT_PORT,
    graph=None,
    host: str = "127.0.0.1",
    **graph_kwargs,
) -> Optional[ThreadingHTTPServer]:
    """노트북 셀에서 호출 — langgraph 그래프를 백그라운드 스레드로 서빙합니다.

    graph 를 주지 않으면 build_chat_graph(**graph_kwargs) 로 기본 그래프(deepagents+Claude)를
    만듭니다(ANTHROPIC_API_KEY env 필요). 포트 충돌은 우아하게 처리합니다.
    """
    if port in _servers:
        print(f"이미 이 커널에서 실행 중입니다: http://{host}:{port}")
        return _servers[port]
    if _is_our_server(host, port):
        print(
            f"이미 챗봇 서버가 http://{host}:{port} 에 떠 있습니다(다른 커널/프로세스). "
            "그대로 우측 💬 탭을 쓰면 됩니다."
        )
        return None

    graph = graph or build_chat_graph(**graph_kwargs)
    try:
        httpd = ThreadingHTTPServer((host, port), _make_handler(graph))
    except OSError as exc:
        print(
            f"⚠️ 포트 {port} 를 열 수 없습니다: {exc}\n"
            f"   stop_graph_server({port}) 후 다시 시도하거나, 커널 재시작 후 재시도하세요."
        )
        return None

    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    _servers[port] = httpd
    print(f"✅ langgraph 챗봇 서버 시작: http://{host}:{port}  — 우측 💬 탭에서 대화하세요.")
    return httpd


def stop_graph_server(port: int = DEFAULT_PORT) -> None:
    """띄운 서버를 중지합니다."""
    httpd = _servers.pop(port, None)
    if httpd is not None:
        httpd.shutdown()
        print(f"중지됨: 포트 {port}")
    else:
        print(f"실행 중인 서버가 없습니다: 포트 {port}")


# ===== Example Usage =====
if __name__ == "__main__":
    # ANTHROPIC_API_KEY 가 env 에 있어야 동작. 포그라운드로 띄워 Ctrl+C 까지.
    _graph = build_chat_graph()
    _httpd = ThreadingHTTPServer(("127.0.0.1", DEFAULT_PORT), _make_handler(_graph))
    print(f"http://127.0.0.1:{DEFAULT_PORT} 에서 동작 중 (Ctrl+C 로 종료)")
    try:
        _httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.")
