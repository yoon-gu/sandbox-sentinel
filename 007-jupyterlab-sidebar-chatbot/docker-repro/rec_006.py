"""006 sql-tui-runner 데모 녹화 — JupyterLab xterm 안 Textual SQL TUI.

pod 의 JupyterLab 터미널(xterm)에서 basic_usage.py 실행 → Textual 풀스크린 SQL TUI
(좌 Entity 트리 + 우 SQL 에디터 + 결과 DataTable). 키보드로 구동. 모델 불필요.

산출: 006-sql-tui-runner/demo.webp
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pw_record import recorder, webm_to_webp, settle, jlab_open_terminal, term_send  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "006-sql-tui-runner" / "demo.webp"


def main():
    with recorder("/tmp/rec_006", viewport={"width": 1440, "height": 900}, headless=False) as (page, get_video):
        jlab_open_terminal(page)
        term_send(page, "python 006-sql-tui-runner/basic_usage.py", settle_ms=4500)

        # 초기 상태(엔티티 트리 + 사전입력 쿼리 + tree-sitter 색) 충분히 보여주기
        settle(page, 3000)
        # 사전 입력된 쿼리 실행 (Ctrl+R — xterm 이 preventDefault 해 브라우저 reload 안 됨)
        page.keyboard.press("Control+r")
        settle(page, 3500)  # 결과 DataTable 렌더
        settle(page, 1500)
        # 종료
        page.keyboard.press("Control+x")
        settle(page, 1500)

    webm = get_video()
    assert webm and Path(webm).exists(), "webm 미생성"
    webp = webm_to_webp(webm, OUT, fps=12, width=1280)
    print(f"✅ 006 demo.webp 생성: {webp} ({webp.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
