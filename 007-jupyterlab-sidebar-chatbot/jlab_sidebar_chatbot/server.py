"""
langgraph 그래프를 localhost HTTP 로 서빙하는 얕은 전송 계층.

두뇌 로직은 graph.py 의 langgraph 그래프(deepagents + 체크포인터)가 전담합니다.
이 파일은 노트북 셀에서 띄우는 작은 stdlib http.server 로, 우측 사이드바 프론트가
호출하는 /chat·/reset·/health 만 제공합니다(직접 만든 LLM 클래스는 없습니다).

엔드포인트:
    POST  /chat         {session_id, message} -> {role, answer, steps}  (한 번에 응답)
    POST  /chat/stream  {session_id, message} -> SSE (text/event-stream)
            event: token  data: {text}            최종 답변 토큰 조각 (여러 번)
            event: done   data: {answer, steps}   끝에 한 번 — 권위 있는 최종 결과
            event: error  data: {error}           실패 시
    POST  /reset        {session_id}          -> {ok: true}   (해당 세션 thread 를 새로 분기)
    GET   /health                              -> {ok: true}

⚠️ 온라인/개발 전용 (graph.py 가 OpenAI 호환 모델 호출). 브라우저 127.0.0.1 == 커널 127.0.0.1 전제.
사용 (노트북 셀):
    from jlab_sidebar_chatbot import start_graph_server

    # 가장 단순 — 환경변수에서 OPENAI_API_KEY/_BASE_URL/_MODEL 을 읽음
    start_graph_server()

    # 사내 vLLM 을 한 줄로 — 인자가 env 보다 우선
    start_graph_server(
        api_key="<사내 vLLM 키>",
        base_url="https://<사내 vllm host>/v1",
        model="<served-model-name>",
    )
"""

from __future__ import annotations

import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Optional

from .graph import build_chat_graph, run_turn, stream_turn

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

        def _sse_start(self):
            """SSE 응답 시작 — Content-Length 없이 열어두고 조각마다 flush 합니다."""
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            # 한 번의 턴만 흘리고 끝나는 일회성 스트림입니다. keep-alive 로 소켓을
            # 열어두면, Content-Length 없는 응답을 받은 브라우저 fetch 가 '끝'을
            # 인지하지 못해(EOF 없음) 최종 렌더가 멈춥니다. 그래서 응답 직후 닫아
            # 깨끗한 EOF 를 보장합니다('done' 이벤트 + 연결 종료 = 이중 종료 신호).
            self.send_header("Connection", "close")
            self.close_connection = True
            # 일부 프록시가 버퍼링해 스트리밍이 끊겨 보이는 것 방지
            self.send_header("X-Accel-Buffering", "no")
            self._cors()
            self.end_headers()

        def _sse_event(self, event: str, payload: dict):
            """SSE 한 프레임(event/data) 을 보내고 즉시 flush 합니다."""
            data = json.dumps(payload, ensure_ascii=False)
            frame = f"event: {event}\ndata: {data}\n\n".encode("utf-8")
            self.wfile.write(frame)
            self.wfile.flush()

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

            if path.endswith("/chat/stream"):
                message = (body.get("message") or "").strip()
                if not message:
                    self._json(400, {"error": "message 가 비어 있습니다"})
                    return
                # SSE 시작 후에는 헤더를 못 바꾸므로, 에러도 event: error 로 흘려보냅니다.
                self._sse_start()
                try:
                    for ev in stream_turn(graph, thread_id(session_id), message):
                        etype = ev.pop("type")  # 'token' | 'done'
                        self._sse_event(etype, ev)
                except (BrokenPipeError, ConnectionResetError):
                    # 클라이언트가 먼저 끊음 — 조용히 종료
                    pass
                except Exception as exc:  # 그래프/LLM 호출 실패를 스트림으로 전달
                    try:
                        self._sse_event("error", {"error": f"그래프 호출 실패: {exc}"})
                    except Exception:
                        pass
            elif path.endswith("/chat"):
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
    *,
    provider: Optional[str] = None,
    thinking: bool = False,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    system_prompt: Optional[str] = None,
    **graph_kwargs,
) -> Optional[ThreadingHTTPServer]:
    """노트북 셀에서 호출 — langgraph 그래프를 백그라운드 스레드로 서빙합니다.

    두 가지 방식:
      1) graph 를 직접 만들어서 넘기기 — `start_graph_server(graph=my_graph)`
      2) (편의) provider/thinking/api_key/base_url/model/system_prompt 를 인자로 주거나,
         환경변수(CHAT_PROVIDER/OPENAI_API_KEY/_BASE_URL/_MODEL/OLLAMA_MODEL 등)로
         주입 → 내부에서 build_chat_graph 호출

    사내 vLLM 한 줄 예시(OpenAI 호환):
        start_graph_server(api_key="...", base_url="https://vllm.사내/v1", model="<name>")
    로컬 Ollama 예시(네이티브 · 생각 끄고 가장 빠르게 · 키 불필요):
        start_graph_server(provider="ollama", model="qwen3.5:0.8b")

    포트가 이미 우리 서버면 그대로 두고 알림, 다른 프로세스가 쓰면 우아하게 실패합니다.
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

    graph = graph or build_chat_graph(
        model=model,
        system_prompt=system_prompt,
        provider=provider,
        thinking=thinking,
        api_key=api_key,
        base_url=base_url,
        **graph_kwargs,
    )
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


def start_test_server(
    port: int = DEFAULT_PORT,
    host: str = "127.0.0.1",
    answer: Optional[str] = None,
) -> Optional[ThreadingHTTPServer]:
    """LLM 호출 없이 자가진단용 테스트 서버를 host:port 에 엽니다 (실제 모델 호출 0).

    build_chat_graph / deepagents / LLM 을 전혀 거치지 않고, 고정 문자열만 돌려주는
    더미 그래프(_StubGraph)를 그대로 같은 전송 계층(라우트·CORS·SSE)에 얹습니다.
    → 사이드바 💬 탭이 이 IP:포트에 '연결되는지'만 분리해서 점검할 때 쓰세요.
      (응답 내용이 아니라 '연결'을 보는 용도. answer 로 회신 문구를 바꿀 수 있습니다.)

    예) start_test_server()                      # 127.0.0.1:8765, 기본 고정 문구
        start_test_server(answer="서버 연결 OK")  # 회신 문구 지정

    중지는 stop_graph_server(port) 로 동일하게 합니다.
    """
    from .graph import _StubGraph

    httpd = start_graph_server(graph=_StubGraph(answer), host=host, port=port)
    if httpd is not None:
        print(f"   (테스트 서버 — LLM 호출 없음. 응답은 고정 문자열. 중지: stop_graph_server({port}))")
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
    # OPENAI_API_KEY 가 env 에 있어야 동작 (사내 vLLM 이면 OPENAI_BASE_URL 도). 포그라운드.
    _graph = build_chat_graph()
    _httpd = ThreadingHTTPServer(("127.0.0.1", DEFAULT_PORT), _make_handler(_graph))
    print(f"http://127.0.0.1:{DEFAULT_PORT} 에서 동작 중 (Ctrl+C 로 종료)")
    try:
        _httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.")
