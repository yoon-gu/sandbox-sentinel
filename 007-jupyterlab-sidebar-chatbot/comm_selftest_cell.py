# ============================================================================
# 🧪 자가진단용 '복붙' 셀 — LLM 없이 고정 텍스트를 돌려주는 챗봇 Comm
# ----------------------------------------------------------------------------
# 사내망(폐쇄망) JupyterLab 노트북 셀에 '이 파일 내용을 통째로' 복사-붙여넣기 해서
# 실행하세요. 패키지 함수(register_chatbot_comm)도, deepagents/LLM 도 전혀 안 씁니다.
# 표준 라이브러리 + ipykernel(Comm) 만으로 챗봇 Comm target 을 등록합니다.
#
# 목적: 우측 💬 사이드바가 '이 커널' 에 Comm 으로 붙는지(= 전송 경로)만 점검.
#       프론트가 보낸 메시지에 고정 텍스트를 토큰 스트리밍으로 돌려줍니다.
#       (전제: jlab-sidebar-chatbot 확장이 설치돼 우측 💬 탭이 보이는 상태)
# ============================================================================
import re
from IPython import get_ipython

# ⚠️ 프론트엔드(labextension)의 target_name 과 '반드시' 일치해야 합니다.
COMM_TARGET = "jlab_sidebar_chatbot"

# 돌려줄 고정 텍스트(마크다운 + 코드블록 — 렌더/복사버튼까지 같이 점검).
ANSWER = (
    "✅ 커널 Comm 연결 OK — 이 응답은 **LLM 없이** 고정 텍스트로 돌려준 것입니다.\n\n"
    "사이드바 💬 가 이 노트북 커널에 정상적으로 붙었다는 뜻입니다.\n\n"
    "```python\n"
    "def add(a, b):\n"
    "    return a + b\n\n"
    "print(add(1, 1))  # 2\n"
    "```\n"
)


def _on_open(comm, open_msg):
    """프론트가 comm 을 열 때 호출 — 핸들러를 붙이고 'ready' 를 보냅니다."""

    def _handler(msg):
        # 프론트가 보낸 페이로드는 msg['content']['data'] 에 들어 있습니다.
        data = (msg.get("content") or {}).get("data") or {}
        mtype = data.get("type")

        if mtype == "reset":  # 새 대화 — 여기선 상태가 없으니 확인만
            comm.send({"type": "reset_ok", "session_id": data.get("session_id", "default")})
            return

        if mtype == "message":  # 한 턴 — 고정 텍스트를 토큰 조각으로 흘린 뒤 done
            for piece in re.findall(r".{1,8}", ANSWER, re.S):
                comm.send({"type": "token", "text": piece})
            comm.send({"type": "done", "answer": ANSWER, "steps": []})
            return

        comm.send({"type": "error", "error": f"알 수 없는 type: {mtype!r}"})

    comm.on_msg(_handler)
    comm.send({"type": "ready"})  # 채널 살아있음 신호


# 현재 노트북 커널에 target 등록(같은 이름으로 다시 실행하면 교체됩니다).
_kernel = get_ipython().kernel
_kernel.comm_manager.register_target(COMM_TARGET, _on_open)
print(f"✅ 테스트 Comm 등록됨: target='{COMM_TARGET}' (LLM 없음 · 고정 텍스트).")
print("   우측 💬 탭에서 아무 메시지나 보내면 고정 텍스트가 토큰 스트리밍으로 옵니다.")
print("   해제: get_ipython().kernel.comm_manager.unregister_target('jlab_sidebar_chatbot', _on_open)")
