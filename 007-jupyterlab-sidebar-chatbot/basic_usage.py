"""
basic_usage.py — 챗봇 "두뇌"만 독립적으로 돌려보는 예제 (JupyterLab/빌드 불필요)

이 변환물의 본체는 JupyterLab 우측 사이드바 익스텐션이지만, 핵심 로직인
멀티턴 대화 두뇌(jlab_sidebar_chatbot/llm.py)는 표준 라이브러리만으로 작성되어
JupyterLab 없이도 단독 실행·검증할 수 있습니다.

실행:
    python basic_usage.py
"""

import sys
from pathlib import Path

# 같은 폴더의 패키지를 import 할 수 있도록 경로 추가(설치 없이 실행하기 위함).
sys.path.insert(0, str(Path(__file__).resolve().parent))

from jlab_sidebar_chatbot.llm import ChatBrain, LLMAdapter, MockLLM  # noqa: E402


def demo_mock_multiturn():
    """기본 MockLLM 으로 멀티턴 맥락이 유지되는지 확인."""
    print("=" * 56)
    print(" 1) 기본 두뇌(MockLLM) 멀티턴 데모")
    print("=" * 56)
    brain = ChatBrain(system_prompt="너는 폐쇄망 데모 도우미야.")
    sid = "session-A"
    for line in ["안녕하세요", "이거 어떻게 쓰나요?", "고맙습니다"]:
        ans = brain.send(sid, line)
        print(f"🙂 user: {line}")
        print(f"🤖 bot : {ans['content']}\n")

    print("-- 누적 기록 (history) --")
    for m in brain.history(sid):
        print(f"  {m['role']:>9}: {m['content']}")

    print("\n-- 세션 격리 확인: 다른 session-B 는 맥락이 비어 있음 --")
    print("  session-B history:", brain.history("session-B"))

    print("\n-- reset 후 기록 비워짐 확인 --")
    brain.reset(sid)
    print("  session-A history:", brain.history(sid))


def demo_custom_adapter():
    """사내 LLM 어댑터 교체가 어떻게 되는지 보여주는 최소 예시."""
    print("\n" + "=" * 56)
    print(" 2) 어댑터 교체 데모 (사내 LLM 자리에 대문자 변환기를 끼움)")
    print("=" * 56)

    class UpperEchoLLM(LLMAdapter):
        """실제 LLM 대신, 직전 사용자 발화를 대문자로 돌려주는 더미 어댑터."""

        def generate(self, messages):
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    return (msg.get("content") or "").upper()
            return "(빈 입력)"

    brain = ChatBrain(adapter=UpperEchoLLM())
    ans = brain.send("session-C", "hello closed network")
    print("🙂 user: hello closed network")
    print(f"🤖 bot : {ans['content']}")
    assert ans["content"] == "HELLO CLOSED NETWORK"
    print("\n✅ 어댑터 한 줄 교체로 두뇌 동작이 바뀌는 것을 확인했습니다.")


if __name__ == "__main__":
    demo_mock_multiturn()
    demo_custom_adapter()
    print("\n모든 데모 완료. 사이드바 UI 체험은 README 의 빌드/실행 안내를 참고하세요.")
