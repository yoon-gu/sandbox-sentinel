"""
챗봇 두뇌 (LLM 어댑터 + Mock + 멀티턴 메모리)

원본 출처(개념 참고):
    - 001-langgraph-notebook-chatbot 의 "Mock LLM + 교체 가능한 어댑터 + 멀티턴" 패턴.
      단, 폐쇄망 서버 익스텐션에서 의존성을 최소화하기 위해 langgraph 를 쓰지 않고
      표준 라이브러리만으로 동일한 사용 감각을 재구현했습니다.
라이선스: BSD-3-Clause (jlab-sidebar-chatbot 자체 코드)
생성: Code Conversion Agent

이 모듈은 **외부 네트워크를 전혀 호출하지 않습니다.** 기본 두뇌(`MockLLM`)는
규칙 기반 에코 응답만 생성하므로, 모델 weight 나 인터넷 없이도 챗봇 전체 흐름
(프론트 사이드바 → 서버 핸들러 → 두뇌 → 응답)을 그대로 체험할 수 있습니다.

실제 사내 LLM 으로 바꾸려면 `LLMAdapter` 를 구현한 클래스를 하나 만들어
`ChatBrain(adapter=MyLocalLLM())` 로 주입하면 됩니다. (파일 하단 예시 참고)
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Dict, List


# 대화 한 줄(턴)을 표현하는 단순 구조.
# role 은 "system" | "user" | "assistant" 중 하나, content 는 실제 텍스트입니다.
Message = Dict[str, str]


# ===== 1. 어댑터 인터페이스 =====
class LLMAdapter(ABC):
    """모든 LLM 백엔드가 따라야 하는 최소 계약.

    `generate` 하나만 구현하면 됩니다. 입력은 지금까지의 대화 전체(messages)이고,
    출력은 assistant 가 할 다음 답변(문자열) 한 개입니다.

    이 인터페이스 덕분에 Mock → 사내 실제 LLM 교체가 "한 줄" 로 끝납니다.
    """

    @abstractmethod
    def generate(self, messages: List[Message]) -> str:
        """대화 기록을 받아 다음 assistant 응답 텍스트를 돌려줍니다."""
        raise NotImplementedError


# ===== 2. 기본 두뇌: 외부 모델 없이 동작하는 Mock =====
class MockLLM(LLMAdapter):
    """규칙 기반 에코 챗봇.

    외부 모델/네트워크 없이도 멀티턴 흐름을 시연할 수 있도록 만든 더미 구현입니다.
    - 인사말에는 인사로 응답
    - "안녕"/"hello" 등 간단한 키워드에 반응
    - 그 외에는 사용자의 말을 되짚어주며 몇 번째 턴인지 알려줌

    실제 운영에서는 이 클래스를 사내 LLM 어댑터로 교체하세요.
    """

    def generate(self, messages: List[Message]) -> str:
        # 가장 최근 사용자 발화만 꺼냅니다(없으면 빈 문자열).
        last_user = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                last_user = (msg.get("content") or "").strip()
                break

        # 지금까지 user 가 말한 횟수(현재 발화 포함) = 몇 번째 턴인지.
        user_turns = sum(1 for m in messages if m.get("role") == "user")

        text = last_user.lower()
        if any(greet in text for greet in ("안녕", "hello", "hi", "반가")):
            return "안녕하세요! 무엇을 도와드릴까요? (저는 외부 모델 없이 동작하는 데모 챗봇입니다)"
        if last_user.endswith("?") or "?" in last_user:
            return (
                f'"{last_user}" 라고 물어보셨네요. 지금은 Mock 응답이라 실제 답을 드리진 못하지만, '
                "사내 LLM 어댑터를 연결하면 여기로 진짜 답변이 들어옵니다."
            )
        if not last_user:
            return "메시지가 비어 있어요. 무엇이든 입력해 보세요."
        return f'[{user_turns}번째 턴] 방금 "{last_user}" 라고 하셨습니다. 계속 말씀해 주세요.'


# ===== 3. 멀티턴 대화 관리 =====
class ChatBrain:
    """세션별 대화 기록을 들고 다니며 어댑터에 위임하는 얇은 관리자.

    - `session_id` 별로 독립된 대화 맥락을 유지합니다(001 의 thread_id 개념과 동일).
    - `send()` 한 번이 한 턴: 사용자 발화를 기록 → 어댑터에 전체 맥락 전달 →
      받은 응답을 기록 → 응답 텍스트 반환.
    - 기록은 메모리에만 둡니다(바이너리 파일 영속화 없음 — 폐쇄망 원칙 준수).
    """

    def __init__(self, adapter: LLMAdapter | None = None, system_prompt: str | None = None):
        # adapter 를 주지 않으면 외부 모델 없이 바로 돌아가는 MockLLM 을 씁니다.
        self.adapter: LLMAdapter = adapter or MockLLM()
        self.system_prompt = system_prompt
        # session_id -> 메시지 리스트
        self._sessions: Dict[str, List[Message]] = {}

    def _history(self, session_id: str) -> List[Message]:
        """세션의 기록을 가져오되, 처음이면 system 프롬프트로 초기화합니다."""
        if session_id not in self._sessions:
            history: List[Message] = []
            if self.system_prompt:
                history.append({"role": "system", "content": self.system_prompt})
            self._sessions[session_id] = history
        return self._sessions[session_id]

    def send(self, session_id: str, user_text: str) -> Message:
        """한 턴을 처리하고 assistant 메시지(dict)를 돌려줍니다.

        반환 형태: {"role": "assistant", "content": ..., "ts": <epoch초>}
        ts 는 프론트에서 메시지 정렬/표시에 쓰기 좋게 함께 내려줍니다.
        """
        history = self._history(session_id)
        history.append({"role": "user", "content": user_text})

        # 실제 응답 생성은 전적으로 어댑터에 위임합니다.
        reply_text = self.adapter.generate(history)

        reply: Message = {"role": "assistant", "content": reply_text}
        history.append(reply)
        # 타임스탬프는 기록에는 남기지 않고 응답에만 덧붙입니다(맥락 오염 방지).
        return {**reply, "ts": time.time()}

    def history(self, session_id: str) -> List[Message]:
        """해당 세션의 전체 대화 기록(system 제외) 사본을 반환합니다."""
        return [m for m in self._history(session_id) if m.get("role") != "system"]

    def reset(self, session_id: str) -> None:
        """해당 세션의 대화 맥락을 초기화합니다(새 대화 시작)."""
        self._sessions.pop(session_id, None)


# ===== 4. Example Usage =====
if __name__ == "__main__":
    # 외부 모델 없이 멀티턴이 유지되는지 바로 확인해 봅니다.
    brain = ChatBrain(system_prompt="너는 폐쇄망 데모 도우미야.")
    sid = "demo-session"
    for line in ["안녕하세요", "오늘 뭐하면 좋을까요?", "고마워"]:
        ans = brain.send(sid, line)
        print(f"🙂 user: {line}")
        print(f"🤖 bot : {ans['content']}\n")

    print("---- 누적 기록 ----")
    for m in brain.history(sid):
        print(f"{m['role']:>9}: {m['content']}")

    # ── 실제 사내 LLM 으로 교체하는 방법 (예시 골격) ──
    #
    # class MyLocalLLM(LLMAdapter):
    #     def __init__(self):
    #         # 사내 모델 로드 / 로컬 추론 서버 클라이언트 초기화 등
    #         ...
    #     def generate(self, messages):
    #         # messages: [{"role": "...", "content": "..."}, ...]
    #         # → 사내 모델 호출 후 답변 문자열만 반환
    #         ...
    #
    # brain = ChatBrain(adapter=MyLocalLLM())
