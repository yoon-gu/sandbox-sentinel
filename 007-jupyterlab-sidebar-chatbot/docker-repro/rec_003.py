"""003 langgraph-chat-repl 데모 녹화 — JupyterLab xterm 안 Textual TUI.

pod 의 JupyterLab 내장 터미널(xterm)에서 basic_usage.py 를 실행해 Textual 풀스크린
REPL 을 띄우고 키보드로 구동한다. MockLLM 내장이라 모델 불필요.

산출: 003-langgraph-chat-repl/demo.webp
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pw_record import recorder, webm_to_webp, settle, jlab_open_terminal, term_send  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "003-langgraph-chat-repl" / "demo.webp"


def main():
    with recorder("/tmp/rec_003", viewport={"width": 1440, "height": 900}, headless=False) as (page, get_video):
        jlab_open_terminal(page)
        # 터미널 폭 확보 후 TUI 실행
        term_send(page, "python 003-langgraph-chat-repl/basic_usage.py", settle_ms=4000)

        # 일반 대화
        term_send(page, "안녕! 한 줄 자기소개 부탁해", settle_ms=2500)
        # tool 호출
        term_send(page, "12 + 7 + 100 계산해줘", settle_ms=2500)
        # 슬래시 명령 팔레트 살짝 보여주기 (클릭 없이 키만)
        page.keyboard.type("/", delay=40)
        settle(page, 1500)
        page.keyboard.press("Backspace")
        settle(page, 500)
        # 종료
        term_send(page, "/quit", settle_ms=2000)

    webm = get_video()
    assert webm and Path(webm).exists(), "webm 미생성"
    webp = webm_to_webp(webm, OUT, fps=12, width=1280)
    print(f"✅ 003 demo.webp 생성: {webp} ({webp.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
