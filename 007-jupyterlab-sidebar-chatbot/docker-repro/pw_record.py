"""Playwright 녹화 하니스 — pod(JupyterLab 8888) 안 변환물 데모를 영상으로 녹화.

sandbox 표준 검증 env(폐쇄망 Pod 재현: ingress 8888, jupyter 기본 url)에서 각
변환물을 브라우저로 구동하며 chromium 영상을 녹화하고, retina(2x) 고해상도
애니메이션 webp 로 변환합니다. (메모리: 데모 webp 는 2배 캡처)

요구: .venv 에 playwright(+chromium), 시스템 ffmpeg.
  python -m playwright install chromium   # 이미 설치돼 있으면 생략

이 파일은 '재사용 헬퍼'입니다. 도구별 상호작용은 rec_<tool>.py 가 import 해서 씁니다.
"""
from __future__ import annotations
import contextlib
import subprocess
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, Page

BASE = "http://127.0.0.1:8888/"
TOKEN = "demo"
# 2x retina: 논리 1280x800, device_scale_factor=2 → 실제 2560x1600 픽셀 캡처.
VIEWPORT = {"width": 1280, "height": 800}
SCALE = 2


@contextlib.contextmanager
def recorder(out_dir: str | Path, *, viewport=VIEWPORT, scale=SCALE):
    """chromium 을 영상 녹화 모드로 띄우고 page 를 yield. 종료 시 webm 경로를 반환.

    사용:
        with recorder("/tmp/rec") as (page, get_video):
            ... page 조작 ...
        webm = get_video()
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    holder = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        context = browser.new_context(
            viewport=viewport, device_scale_factor=scale,
            record_video_dir=str(out), record_video_size=viewport,
        )
        page = context.new_page()
        try:
            yield page, lambda: holder.get("webm")
        finally:
            video = page.video
            context.close()        # close 후에야 영상 파일이 확정됨
            browser.close()
            if video:
                holder["webm"] = video.path()


def webm_to_webp(webm: str | Path, webp: str | Path, *, fps=12, width=0, quality=70):
    """녹화 webm 을 애니메이션 webp 로 변환(retina 유지). width=0 이면 원본 크기.

    시스템 ffmpeg 에 libwebp 인코더가 없어, ffmpeg 로 프레임(png)을 뽑고 libwebp 의
    img2webp 로 애니메이션 webp 를 조립합니다. width 를 주면 그 폭으로 다운스케일
    (2x 캡처라 원본 폭의 절반을 주면 논리 해상도지만 선명).

    ponytail: 프레임을 한 번에 img2webp 인자로 넘깁니다. 수백 프레임(수십 초)까지는
    OK 이나, 아주 긴 녹화는 ARG_MAX 에 걸릴 수 있음 — 그때 배치 분할/파일목록 도입.
    """
    import glob
    import tempfile
    frames_dir = Path(tempfile.mkdtemp())
    vf = f"fps={fps}" + (f",scale={width}:-1:flags=lanczos" if width else "")
    subprocess.run(["ffmpeg", "-y", "-i", str(webm), "-vf", vf,
                    str(frames_dir / "f_%04d.png")], check=True, capture_output=True)
    frames = sorted(glob.glob(str(frames_dir / "f_*.png")))
    if not frames:
        raise RuntimeError("프레임 추출 실패 (빈 영상?)")
    dur = int(round(1000 / fps))
    subprocess.run(["img2webp", "-loop", "0", "-lossy", "-q", str(quality),
                    "-d", str(dur), *frames, "-o", str(webp)], check=True, capture_output=True)
    return Path(webp)


def jlab_goto(page: Page, path: str):
    """JupyterLab/파일 경로를 토큰과 함께 연다."""
    sep = "&" if "?" in path else "?"
    page.goto(f"{BASE}{path}{sep}token={TOKEN}", wait_until="domcontentloaded")


def wait_text(page: Page, selector: str, contains: str, timeout=120_000):
    """selector 의 텍스트에 contains 가 나타날 때까지 대기."""
    page.wait_for_function(
        "([s, t]) => { const e = document.querySelector(s); return e && e.textContent.includes(t); }",
        arg=[selector, contains], timeout=timeout,
    )


def settle(page: Page, ms=800):
    page.wait_for_timeout(ms)


if __name__ == "__main__":
    # 자가점검: 빈 페이지를 잠깐 녹화해 webm→webp 파이프라인이 도는지 확인.
    import tempfile
    d = tempfile.mkdtemp()
    with recorder(d) as (page, get_video):
        page.set_content("<h1 style='font:48px sans-serif'>recorder self-check</h1>")
        settle(page, 1500)
    webm = get_video()
    assert webm and Path(webm).exists(), "webm 미생성"
    webp = webm_to_webp(webm, Path(d) / "selfcheck.webp")
    assert webp.exists() and webp.stat().st_size > 0, "webp 변환 실패"
    print("✅ 하니스 자가점검 통과:", webp, webp.stat().st_size, "bytes")
