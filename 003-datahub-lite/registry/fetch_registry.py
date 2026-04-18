"""실 DataHub 인스턴스에서 엔티티/aspect 레지스트리를 가져와 datahub_lite.py 에
임베드 가능한 Python 리터럴로 변환합니다.

다음 명령으로 datahub_lite.py 의 ENTITY_REGISTRY/TIMESERIES_ASPECTS 블록을 갱신할 때 사용:
  python registry/fetch_registry.py \\
      --frontend http://localhost:9002 \\
      --user datahub --password datahub \\
      --out registry/registry_data.py

생성된 registry_data.py 의 내용을 datahub_lite.py 의 동명 블록에 그대로 붙여넣으면 됩니다.

표준 라이브러리만 사용 (urllib).
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path


def login(frontend: str, user: str, password: str) -> http.cookiejar.CookieJar:
    """DataHub 프론트엔드에 로그인하여 세션 쿠키 획득."""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    body = json.dumps({"username": user, "password": password}).encode("utf-8")
    req = urllib.request.Request(
        f"{frontend.rstrip('/')}/logIn",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with opener.open(req, timeout=15) as resp:
        if resp.status != 200:
            raise SystemExit(f"login 실패: HTTP {resp.status}")
    urllib.request.install_opener(opener)
    return jar


def fetch_paged(url: str, count: int = 1000) -> list[dict]:
    """`elements` 페이지 응답을 단순 list 로 모아서 반환."""
    sep = "&" if "?" in url else "?"
    full = f"{url}{sep}count={count}"
    with urllib.request.urlopen(full, timeout=30) as resp:
        d = json.load(resp)
    return d.get("elements", [])


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--frontend", default="http://localhost:9002",
                   help="DataHub 프론트엔드 URL (기본 http://localhost:9002)")
    p.add_argument("--user", default="datahub")
    p.add_argument("--password", default="datahub")
    p.add_argument("--out", default=str(Path(__file__).resolve().parent / "registry_data.py"))
    args = p.parse_args()

    print(f"DataHub 로그인: {args.frontend} as {args.user!r}")
    login(args.frontend, args.user, args.password)

    print("엔티티 레지스트리 조회…")
    ents = fetch_paged(f"{args.frontend}/openapi/v1/registry/models/entity/specifications")
    print(f"  → {len(ents)} entities")

    print("aspect 레지스트리 조회…")
    asps = fetch_paged(f"{args.frontend}/openapi/v1/registry/models/aspect/specifications")
    print(f"  → {len(asps)} aspects")

    # 정리
    reg: dict[str, dict] = {}
    for e in ents:
        name = e["name"]
        a_set = sorted({a["aspectAnnotation"]["name"] for a in e.get("aspectSpecs", [])})
        a_set.append(e["keyAspectName"])
        a_set = sorted(set(a_set))
        reg[name] = {"keyAspect": e["keyAspectName"], "aspects": tuple(a_set)}

    ts = sorted({a["aspectAnnotation"]["name"] for a in asps if a["aspectAnnotation"].get("timeseries")})

    # Python 리터럴로 출력
    out_lines = [
        f"# Auto-generated from DataHub at {args.frontend}",
        f"# {len(reg)} entities, {sum(len(v['aspects']) for v in reg.values())} entity-aspect pairs, {len(ts)} timeseries aspects",
        "",
        "ENTITY_REGISTRY = {",
    ]
    for name in sorted(reg):
        spec = reg[name]
        out_lines.append(
            f"    {name!r}: {{'keyAspect': {spec['keyAspect']!r}, 'aspects': {spec['aspects']!r}}},"
        )
    out_lines.append("}")
    out_lines.append("")
    out_lines.append(f"TIMESERIES_ASPECTS = frozenset({ts!r})")
    out_lines.append("")
    text = "\n".join(out_lines)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    size_kb = out_path.stat().st_size / 1024
    print(f"\n✓ {out_path} ({size_kb:.1f} KB) 작성 완료")
    print("  datahub_lite.py 의 ENTITY_REGISTRY/TIMESERIES_ASPECTS 블록을 이 파일 내용으로 교체하세요.")


if __name__ == "__main__":
    main()
