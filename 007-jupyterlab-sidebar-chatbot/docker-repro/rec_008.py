"""008 standalone-comm-chatbot 데모 녹화 (pod 안 같은-출처 /files/ chat.html).

stub 제공자(base_url=None)로 녹화 — 모델 없이도 008 의 전 UX(연결→ready→토큰
스트리밍→done→마크다운/접이식 steps)를 결정적으로 보여줍니다. 008 의 본질은
'standalone 클라이언트 ↔ 커널 Comm 전송' 이라 stub 으로 충분(실모델 왕복은 별도 검증).

실행: .venv/bin/python 007-.../docker-repro/rec_008.py
산출: 008-standalone-comm-chatbot/demo.webp
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pw_record import recorder, webm_to_webp, BASE, TOKEN, settle  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "008-standalone-comm-chatbot" / "demo.webp"
PAGE = f"{BASE}files/008-standalone-comm-chatbot/chat.html?token={TOKEN}"


def main():
    tmp = Path("/tmp/rec_008")
    with recorder(tmp) as (page, get_video):
        page.goto(PAGE, wait_until="domcontentloaded")
        settle(page, 800)

        # 연결 패널 채우기 (pod = jupyter 기본 url, stub 제공자)
        page.fill("#url", BASE)
        page.fill("#token", TOKEN)
        page.select_option("#provider", "stub")
        settle(page, 600)

        page.click("#connect")
        # ready → '연결됨' 표시 대기
        page.wait_for_function(
            "document.querySelector('#status').textContent.includes('연결됨')",
            timeout=60_000,
        )
        settle(page, 800)

        # 한 턴 대화
        page.fill("#input", "폐쇄망에서 단일 파일 챗봇을 쓰는 이유를 한 문장으로 알려줘.")
        settle(page, 400)
        page.click("#send")
        # 봇 버블이 충분히 채워질 때까지(done) 대기
        page.wait_for_function(
            "(() => { const b = document.querySelectorAll('.msg.bot'); "
            "return b.length && b[b.length-1].textContent.length > 60; })()",
            timeout=60_000,
        )
        settle(page, 3000)  # 답변 렌더 후 충분히 머물러 읽을 시간(루프 webp 마무리)

    webm = get_video()
    assert webm and Path(webm).exists(), "webm 미생성"
    webp = webm_to_webp(webm, OUT, fps=12, width=1280)  # 2x 캡처 → 1280 폭(선명)
    print(f"✅ 008 demo.webp 생성: {webp} ({webp.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
