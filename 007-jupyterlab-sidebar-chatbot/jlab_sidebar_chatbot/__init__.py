"""
jlab_sidebar_chatbot — JupyterLab 우측 사이드바 챗봇

두 조각으로 이뤄집니다.
  1) 프론트엔드 labextension (TypeScript 빌드 산출물; labextension/ 에 위치)
     → 우측 사이드바 챗 UI. 설치 + 브라우저 새로고침만으로 뜹니다(jupyter 재시작 불필요).
     LangGraph Server 의 thread/run API 를 호출합니다.
  2) 챗봇 두뇌 = langgraph 그래프 (deepagents 로 생성).
     → LangGraph 생태계 네이티브 서빙(`langgraph dev`)으로 띄웁니다. langgraph.json 이
        graph.py:make_graph 를 가리키고, thread 영속화는 LangGraph 플랫폼이 제공합니다.
        직접 만든 LLM 추상화/HTTP 서버는 없습니다.

⚠️ 두뇌는 Claude API 를 쓰는 '온라인/개발 전용'입니다(폐쇄망 배포용 아님).
   API 키는 환경변수 ANTHROPIC_API_KEY 로만 읽습니다.

서빙:  cd <이 폴더> && langgraph dev --allow-blocking      (LangGraph Server, 기본 :2024)

생성: Code Conversion Agent
라이선스: BSD-3-Clause
"""

from ._version import __version__
from .graph import DEFAULT_MODEL, build_chat_graph, make_graph, reply


def _jupyter_labextension_paths():
    """JupyterLab 에게 프론트엔드 자산(빌드된 labextension) 위치를 알려줍니다."""
    return [{"src": "labextension", "dest": "jlab-sidebar-chatbot"}]


__all__ = [
    "__version__",
    "DEFAULT_MODEL",
    "build_chat_graph",
    "make_graph",
    "reply",
    "_jupyter_labextension_paths",
]
