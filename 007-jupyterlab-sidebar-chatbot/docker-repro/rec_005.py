"""005 sql-codemirror-runner 데모 녹화 — 노트북 안 CodeMirror SQL 에디터.

셀 [2](임시 SQLite + import)와 [4](runner.set_query + runner.show())를 실행해
CodeMirror 에디터를 띄우고, 에디터 포커스 → Ctrl+Enter 실행 → 결과 표,
Ctrl+Space 자동완성 popup 을 보여준다. 모델 불필요.

산출: 005-sql-codemirror-runner/demo.webp
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pw_record import recorder, webm_to_webp, settle, jlab_open_notebook  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "005-sql-codemirror-runner" / "demo.webp"
NB = "005-sql-codemirror-runner/demo.ipynb"


def dismiss_news(page):
    try:
        no = page.get_by_role("button", name="No")
        if no.count():
            no.first.click(timeout=2000)
    except Exception:
        pass


def main():
    with recorder("/tmp/rec_005") as (page, get_video):
        jlab_open_notebook(page, NB)
        dismiss_news(page)

        # 첫 코드 셀 선택 후 Shift+Enter ×3 → 코드셀 [2],[4] 실행(사이 markdown [3] 렌더)
        page.locator(".jp-Notebook .jp-CodeCell").first.click()
        for _ in range(3):
            page.keyboard.press("Shift+Enter")
            settle(page, 2500)

        # CodeMirror 에디터 mount 대기 (trusted 상태에서 인라인 script 실행됨)
        page.wait_for_selector(".jp-OutputArea-output .CodeMirror", timeout=60_000)
        editor = page.locator(".jp-OutputArea-output .CodeMirror").first
        editor.scroll_into_view_if_needed()
        settle(page, 1500)

        # 에디터 포커스 후 Ctrl+Enter 실행 → 결과 표
        editor.click()
        settle(page, 600)
        page.keyboard.press("Control+Enter")
        # 결과 표(ipywidgets Output 안 <table>) 대기
        try:
            page.wait_for_selector(".jp-OutputArea-output table", timeout=20_000)
        except Exception:
            pass
        settle(page, 2500)

        # 자동완성 popup 시연: 에디터 끝에서 식별자 타이핑 후 Ctrl+Space
        editor.click()
        page.keyboard.press("Control+End")
        page.keyboard.type("\nWHERE u.", delay=60)
        settle(page, 500)
        page.keyboard.press("Control+Space")
        settle(page, 2500)

        # 마무리: 에디터/결과를 화면에 두고 머무름
        editor.scroll_into_view_if_needed()
        settle(page, 2000)

    webm = get_video()
    assert webm and Path(webm).exists(), "webm 미생성"
    webp = webm_to_webp(webm, OUT, fps=12, width=1280)
    print(f"✅ 005 demo.webp 생성: {webp} ({webp.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
