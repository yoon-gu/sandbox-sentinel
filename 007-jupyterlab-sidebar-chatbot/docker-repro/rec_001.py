"""001 langgraph-notebook-chatbot 데모 녹화 — JupyterLab 노트북 안 ipywidgets 챗 UI.

MockLLM 내장이라 모델 불필요. 첫 코드 셀(bot.chat_ui())을 실행해 위젯을 띄우고,
일반 대화 → tool 호출 → HITL 복수선택을 보여준다.

산출: 001-langgraph-notebook-chatbot/demo.webp
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pw_record import (  # noqa: E402
    recorder, webm_to_webp, settle, jlab_open_notebook, jlab_run_first_code_cell,
)

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "001-langgraph-notebook-chatbot" / "demo.webp"
NB = "001-langgraph-notebook-chatbot/demo.ipynb"

# 챗 UI 위젯 입력칸(셀 output 안)
INPUT = ".jp-OutputArea-output .widget-text input, .jp-OutputArea-output input[type=text]"


def send(page, text):
    """텍스트 입력 후 '보내기' 버튼 클릭(Enter on_submit 은 불안정 → 버튼 사용)."""
    inp = page.locator(INPUT).first
    inp.scroll_into_view_if_needed()
    inp.click()
    page.keyboard.type(text, delay=25)
    settle(page, 500)  # ipywidgets value 동기화 여유
    page.get_by_role("button", name="보내기").first.click()


def dismiss_news(page):
    """JupyterLab 'official Jupyter news?' 팝업이 뜨면 No 로 닫음."""
    try:
        no = page.get_by_role("button", name="No")
        if no.count():
            no.first.click(timeout=2000)
    except Exception:
        pass


def main():
    with recorder("/tmp/rec_001") as (page, get_video):
        jlab_open_notebook(page, NB)
        dismiss_news(page)
        jlab_run_first_code_cell(page)
        # 위젯 렌더 대기(커널 comm 연결 후)
        page.wait_for_selector(INPUT, timeout=90_000)
        dismiss_news(page)
        page.locator(INPUT).first.scroll_into_view_if_needed()
        settle(page, 1500)

        # 1) 일반 대화
        send(page, "안녕! 한 줄로 자기소개 해줘.")
        settle(page, 2500)
        # 2) tool 호출
        send(page, "12 + 7 + 100 계산해줘.")
        settle(page, 2500)
        # 3) HITL 복수선택 트리거
        send(page, "관심 있는 자산군 여러 개 알려줘.")
        settle(page, 2500)

        # 체크박스가 뜨면 2개 체크 후 '답변 제출'
        cbs = page.locator(".jp-OutputArea-output .widget-checkbox input")
        if cbs.count() >= 2:
            cbs.nth(0).click(); settle(page, 400)
            cbs.nth(1).click(); settle(page, 400)
            submit = page.get_by_role("button", name="답변 제출")
            if submit.count():
                submit.first.click()
                settle(page, 2500)
        # 마무리: 챗 위젯을 다시 화면에 두고 대화 결과에 머무름
        page.locator(INPUT).first.scroll_into_view_if_needed()
        settle(page, 2500)

    webm = get_video()
    assert webm and Path(webm).exists(), "webm 미생성"
    webp = webm_to_webp(webm, OUT, fps=12, width=1280)
    print(f"✅ 001 demo.webp 생성: {webp} ({webp.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
