"""
jlab_sidebar_chatbot — JupyterLab 우측 사이드바 챗봇

두 조각으로 이뤄집니다.
  1) 프론트엔드 labextension (TypeScript 빌드 산출물; labextension/ 에 위치)
     → 우측 사이드바 챗 UI. 설치 + 브라우저 새로고침만으로 뜹니다(서버 재시작 불필요).
  2) 챗봇 두뇌 (이 Python 패키지)
     → '노트북 셀에서 띄우는 localhost HTTP 서버'(start_brain_server). jupyter 서버
        익스텐션이 아니므로 jupyter 서버 재시작이 필요 없습니다.

생성: Code Conversion Agent
라이선스: BSD-3-Clause
"""

from ._version import __version__
from .llm import ChatBrain, LLMAdapter, MockLLM
from .server import DEFAULT_PORT, start_brain_server, stop_brain_server


def _jupyter_labextension_paths():
    """JupyterLab 에게 프론트엔드 자산(빌드된 labextension) 위치를 알려줍니다."""
    return [{"src": "labextension", "dest": "jlab-sidebar-chatbot"}]


__all__ = [
    "__version__",
    "ChatBrain",
    "LLMAdapter",
    "MockLLM",
    "DEFAULT_PORT",
    "start_brain_server",
    "stop_brain_server",
    "_jupyter_labextension_paths",
]
