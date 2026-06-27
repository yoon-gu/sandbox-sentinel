"""002 sentinel-track 데모 녹화 — self-contained dashboard.html (pod /files/).

전제: pod 안에서 basic_usage.py 로 002-sentinel-track/dashboard.html 이 생성돼 있어야 함
(docker exec jlsc-repro bash -c 'cd /work/002-sentinel-track && python basic_usage.py').
dashboard.html 은 API 호출/쿠키 없이 인라인 JS 로만 렌더 → /files/ sandbox 에서도 정상.

산출: 002-sentinel-track/demo.webp
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pw_record import recorder, webm_to_webp, BASE, TOKEN, settle  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "002-sentinel-track" / "demo.webp"
PAGE = f"{BASE}files/002-sentinel-track/dashboard.html?token={TOKEN}"


def main():
    with recorder("/tmp/rec_002") as (page, get_video):
        page.goto(PAGE, wait_until="domcontentloaded")
        page.wait_for_selector("button[data-tab=sweep]", timeout=30_000)
        page.wait_for_selector("#main table tr", timeout=30_000)
        settle(page, 1200)

        # 검색 필터: 'adam' → 2개로 좁혀짐 → 지워서 복원
        search = page.query_selector("input.search") or page.query_selector("input[type=search]")
        if search:
            search.click()
            page.keyboard.type("adam", delay=120)
            settle(page, 1400)
            for _ in range(4):
                page.keyboard.press("Backspace")
            settle(page, 900)

        # Run 상세: 첫 run 행 클릭 → 차트
        row = page.query_selector("#main table tbody tr") or page.query_selector("#main table tr:nth-child(2)")
        if row:
            row.click()
            settle(page, 1800)

        # Sweep 탭 → 체크박스 토글
        page.click("button[data-tab=sweep]")
        settle(page, 1500)
        cb = page.query_selector(".runs-checkboxes input[type=checkbox]") or page.query_selector("#main input[type=checkbox]")
        if cb:
            cb.click()
            settle(page, 1200)
            cb.click()
            settle(page, 1000)

        # Runs 탭으로 복귀
        page.click("button[data-tab=runs]")
        settle(page, 1500)

    webm = get_video()
    assert webm and Path(webm).exists(), "webm 미생성"
    webp = webm_to_webp(webm, OUT, fps=12, width=1280)
    print(f"✅ 002 demo.webp 생성: {webp} ({webp.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
