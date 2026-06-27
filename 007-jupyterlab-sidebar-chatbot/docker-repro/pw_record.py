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
def recorder(out_dir: str | Path, *, viewport=VIEWPORT, scale=SCALE, headless=True):
    """chromium 을 영상 녹화 모드로 띄우고 page 를 yield. 종료 시 webm 경로를 반환.

    headless=True: DOM/HTML 페이지(노트북·대시보드·사이드바·chat.html)용 — 안정.
    headless=False: xterm 터미널 TUI 용. xterm 은 canvas 렌더라 old-headless 에선
        백지로 캡처됨 → headed 로 띄워야 화면이 잡힌다(macOS 에 창이 잠깐 뜸).

    사용:
        with recorder("/tmp/rec") as (page, get_video):
            ... page 조작 ...
        webm = get_video()
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    holder = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, args=["--no-sandbox"])
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


def jlab_open_notebook(page: Page, nb_path: str, *, kernel_wait=8000):
    """JupyterLab 에서 노트북을 열고 셸/커널이 자리잡을 때까지 대기.

    nb_path: root_dir(=리포루트) 기준 상대경로. 예: 001-.../demo.ipynb
    """
    # ?reset = 저장된 워크스페이스(이전에 열려있던 탭/레이아웃)를 비우고 시작.
    # 안 하면 직전 녹화의 노트북 탭이 복원돼 두 탭이 겹치고 셀 선택이 엉킨다.
    page.goto(f"{BASE}lab/tree/{nb_path}?reset&token={TOKEN}", wait_until="domcontentloaded")
    page.wait_for_selector(".jp-Notebook", timeout=60_000)
    # 'Select Kernel' 등 다이얼로그가 뜨면 기본값으로 통과
    try:
        btn = page.wait_for_selector(".jp-Dialog .jp-mod-accept", timeout=4000)
        if btn:
            btn.click()
    except Exception:
        pass  # 다이얼로그 없으면 정상
    page.wait_for_timeout(kernel_wait)  # 커널 connecting→idle 여유


def jlab_run_first_code_cell(page: Page):
    """첫 번째 코드 셀을 선택해 Shift+Enter 로 실행."""
    cell = page.locator(".jp-Notebook .jp-CodeCell").first
    cell.click()
    page.keyboard.press("Shift+Enter")


def jlab_open_terminal(page: Page):
    """JupyterLab Launcher 에서 새 터미널(xterm)을 열고 포커스를 준다.

    터미널 안에서 TUI(textual/prompt_toolkit)를 실행해 녹화하기 위함. xterm 은
    캔버스 렌더라 DOM 단언 대신 키 입력(type/press)으로만 구동한다.
    """
    page.goto(f"{BASE}lab?reset&token={TOKEN}", wait_until="domcontentloaded")
    page.wait_for_selector(".jp-Launcher", timeout=60_000)
    # Launcher 의 'Terminal' 카드 클릭
    card = page.locator(".jp-LauncherCard", has_text="Terminal")
    card.first.click()
    page.wait_for_selector(".jp-Terminal .xterm", timeout=30_000)
    page.wait_for_timeout(1500)
    page.locator(".jp-Terminal .xterm-screen").first.click()  # 포커스
    page.wait_for_timeout(500)


def term_send(page: Page, text: str, *, enter=True, settle_ms=800):
    """포커스된 xterm 에 텍스트를 입력(+Enter).

    ⚠️ 클릭하지 않습니다 — 풀스크린 Textual/prompt_toolkit 앱은 마우스 클릭을 받아
    입력 포커스를 딴 위젯으로 옮겨버립니다. 터미널은 jlab_open_terminal 의 최초 클릭
    이후 포커스를 유지하므로, 이후엔 키만 보냅니다.
    """
    page.keyboard.type(text, delay=30)
    if enter:
        page.keyboard.press("Enter")
    page.wait_for_timeout(settle_ms)


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
