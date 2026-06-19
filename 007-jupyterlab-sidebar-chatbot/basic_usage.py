"""
basic_usage.py — langgraph 챗봇 두뇌(그래프)를 단독 실행하는 예제

이 변환물의 두뇌는 deepagents 가 만든 langgraph 그래프 + InMemorySaver 체크포인터입니다.
(직접 만든 LLM 추상화 클래스는 없습니다 — langgraph 그래프 하나가 두뇌입니다.)

⚠️ 온라인/개발 전용: OpenAI 호환 모델(실 OpenAI / 사내 vLLM / 로컬 Ollama) 사용.
   환경변수 OPENAI_API_KEY 가 필요합니다(사내 vLLM/Ollama 면 OPENAI_BASE_URL/MODEL 도).

실행:
    OPENAI_API_KEY=sk-... python basic_usage.py
    # 로컬 Ollama 예시:
    #   OPENAI_BASE_URL=http://localhost:11434/v1 OPENAI_API_KEY=ollama \
    #   OPENAI_MODEL=qwen3.5 python basic_usage.py
"""

import os
import sys
from pathlib import Path

# 같은 폴더의 패키지를 설치 없이 import (소스 우선)
sys.path.insert(0, str(Path(__file__).resolve().parent))


def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print(
            "OPENAI_API_KEY 환경변수가 필요합니다.\n"
            "예:  OPENAI_API_KEY=sk-... python basic_usage.py\n"
            "로컬 Ollama 면:  OPENAI_BASE_URL=http://localhost:11434/v1 "
            "OPENAI_API_KEY=ollama OPENAI_MODEL=qwen3.5 python basic_usage.py"
        )
        return

    from jlab_sidebar_chatbot import build_chat_graph, reply

    graph = build_chat_graph()  # deepagents + ChatOpenAI + InMemorySaver

    print("=== langgraph 그래프 멀티턴 (thread_id 로 맥락 유지) ===")
    tid = "demo-thread"
    for line in ["내 이름은 윤구야. 기억해줘.", "내 이름이 뭐였지?", "고마워!"]:
        print(f"🙂 user: {line}")
        print(f"🤖 bot : {reply(graph, tid, line)}\n")

    print("=== 다른 thread 는 기억을 공유하지 않음 (thread 격리) ===")
    print(f"🤖 bot : {reply(graph, 'other-thread', '내 이름 알아?')}")


if __name__ == "__main__":
    main()
