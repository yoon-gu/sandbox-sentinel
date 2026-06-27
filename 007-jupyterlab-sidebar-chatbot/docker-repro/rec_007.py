"""007 jupyterlab-sidebar-chatbot 데모 녹화 — 우측 사이드바 💬 챗봇.

stub 두뇌(register_chatbot_comm(base_url=None))로 녹화 — 모델 없이도 사이드바가
'이 노트북의 커널'에 Comm 으로 붙어 토큰 스트리밍 → 마크다운 렌더하는 전 경로를 보여준다.
(demo.ipynb 의 register 셀은 녹화 직전 stub 으로 패치하고, 녹화 후 git 으로 복원.)

산출: 007-jupyterlab-sidebar-chatbot/demo.webp
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pw_record import recorder, webm_to_webp, settle, jlab_open_notebook  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "007-jupyterlab-sidebar-chatbot" / "demo.webp"
NB = "007-jupyterlab-sidebar-chatbot/demo.ipynb"
SIDEBAR_TAB = '.jp-SideBar .lm-TabBar-tab[data-id="jlab-sidebar-chatbot-widget"]'
CHAT_INPUT = ".jp-ChatWidget-input, textarea.jp-ChatWidget-input"


def dismiss_news(page):
    try:
        no = page.get_by_role("button", name="No")
        if no.count():
            no.first.click(timeout=2000)
    except Exception:
        pass


def main():
    with recorder("/tmp/rec_007", viewport={"width": 1440, "height": 900}) as (page, get_video):
        jlab_open_notebook(page, NB)
        dismiss_news(page)

        # register 셀(2번째 코드 셀=cell[4]) 실행 → 커널에 stub 두뇌 등록
        page.locator(".jp-Notebook .jp-CodeCell").nth(1).click()
        page.keyboard.press("Shift+Enter")
        page.wait_for_function(
            "document.body.innerText.includes('챗봇 Comm 등록됨')", timeout=60_000)
        settle(page, 1200)

        # 우측 💬 사이드바 탭 열기
        tab = page.locator(SIDEBAR_TAB)
        if tab.count():
            tab.first.click()
        else:
            # 폴백: 캡션/타이틀로 탐색
            page.locator('.jp-SideBar.jp-mod-right .lm-TabBar-tab').last.click()
        page.wait_for_selector(CHAT_INPUT, timeout=30_000)
        settle(page, 1200)

        # 메시지 전송 (Enter=전송)
        inp = page.locator(CHAT_INPUT).first
        inp.click()
        page.keyboard.type("JupyterLab 확장이 뭔지 한 문단으로 설명하고 파이썬 hello world 코드도 보여줘.", delay=20)
        settle(page, 400)
        page.keyboard.press("Enter")

        # assistant 답변 렌더 대기(스트리밍→마크다운)
        page.wait_for_function(
            "(() => { const m = document.querySelector('.jp-ChatWidget-messages'); "
            "return m && m.innerText.length > 80; })()",
            timeout=60_000,
        )
        settle(page, 3500)

    webm = get_video()
    assert webm and Path(webm).exists(), "webm 미생성"
    webp = webm_to_webp(webm, OUT, fps=12, width=1280)
    print(f"✅ 007 demo.webp 생성: {webp} ({webp.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
