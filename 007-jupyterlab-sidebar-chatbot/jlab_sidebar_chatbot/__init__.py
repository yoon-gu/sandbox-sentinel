"""
jlab_sidebar_chatbot — JupyterLab 우측 사이드바 챗봇

두 조각으로 이뤄집니다.
  1) 프론트엔드 labextension (TypeScript 빌드 산출물; labextension/ 에 위치)
     → 우측 사이드바 챗 UI. 설치 + 브라우저 새로고침만으로 뜹니다(jupyter 재시작 불필요).
  2) 챗봇 두뇌 = langgraph 그래프 (deepagents 로 생성) + InMemorySaver 체크포인터.
     → 노트북 셀에서 start_graph_server() 로 localhost 에 서빙. 직접 만든 LLM
        추상화(Adapter/Mock/Brain)는 없고, langgraph 그래프 하나가 두뇌입니다.

⚠️ 두뇌는 Claude API 를 쓰는 '온라인/개발 전용'입니다(폐쇄망 배포용 아님).
   API 키는 환경변수 ANTHROPIC_API_KEY 로만 읽습니다.

생성: Code Conversion Agent
라이선스: BSD-3-Clause
"""

from ._version import __version__
from .graph import DEFAULT_MODEL, build_chat_graph, reply, run_turn
from .server import DEFAULT_PORT, start_graph_server, stop_graph_server


def _jupyter_labextension_paths():
    """JupyterLab 에게 프론트엔드 자산(빌드된 labextension) 위치를 알려줍니다."""
    return [{"src": "labextension", "dest": "jlab-sidebar-chatbot"}]


__all__ = [
    "__version__",
    "DEFAULT_MODEL",
    "build_chat_graph",
    "reply",
    "run_turn",
    "DEFAULT_PORT",
    "start_graph_server",
    "stop_graph_server",
    "_jupyter_labextension_paths",
]
