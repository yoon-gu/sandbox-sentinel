"""로컬 카탈로그(JSONL)를 실제 DataHub 인스턴스로 import.

본체(`datahub_lite.py`)는 외부 의존이 0이지만, 이 export 스크립트는 세 가지 모드를
지원합니다. 사내 환경에 따라 가장 편한 경로를 고르세요.

세 가지 모드
-----------
  1) **frontend** (기본): DataHub 프론트엔드 URL(:9002)과 username/password 만으로 동작.
     stdlib(urllib) 만 사용하며, GMS 프록시 경로(`/api/gms/aspects?action=ingestProposal`)
     를 통해 ingest. 가장 폐쇄망 친화적.
  2) **gms**: GMS 베이스 URL(:8080)과 access token (선택) 으로 직접 ingest.
     stdlib 만 사용. 운영 자동화에 적합.
  3) **acryl**: `acryl-datahub` 패키지가 있는 환경에서 `DatahubRestEmitter` 사용.
     가장 풍부한 검증/타입체크 제공.

사용 예
------
  # 0) 데모 카탈로그 빌드
  python examples/basic_usage.py

  # 1) frontend 모드 (기본)
  python examples/export_to_datahub.py \\
      --jsonl data/basic_mcps.jsonl \\
      --frontend http://localhost:9002 --user datahub --password datahub

  # 2) gms 모드
  python examples/export_to_datahub.py \\
      --jsonl data/basic_mcps.jsonl --mode gms \\
      --gms-url http://localhost:8080 --token "$DH_TOKEN"

  # 3) acryl 모드
  python examples/export_to_datahub.py \\
      --jsonl data/basic_mcps.jsonl --mode acryl \\
      --gms-url http://localhost:8080 --token "$DH_TOKEN"
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ===== 모드 1: frontend (cookie 인증) =====

def _login_frontend(frontend: str, user: str, password: str) -> http.cookiejar.CookieJar:
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
            raise SystemExit(f"frontend 로그인 실패: HTTP {resp.status}")
    urllib.request.install_opener(opener)
    return jar


def emit_via_frontend(mcps: Iterable[dict], frontend: str, user: str, password: str) -> tuple[int, int]:
    _login_frontend(frontend, user, password)
    url = f"{frontend.rstrip('/')}/api/gms/aspects?action=ingestProposal"
    return _post_mcps(mcps, url, headers={"Content-Type": "application/json"})


# ===== 모드 2: GMS 직접 (token 인증) =====

def emit_via_gms(mcps: Iterable[dict], gms_url: str, token: str | None) -> tuple[int, int]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"{gms_url.rstrip('/')}/aspects?action=ingestProposal"
    return _post_mcps(mcps, url, headers=headers)


def _post_mcps(mcps: Iterable[dict], url: str, headers: dict) -> tuple[int, int]:
    n_ok = n_fail = 0
    for d in mcps:
        body = json.dumps({"proposal": d, "async": "false"}).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                if 200 <= resp.status < 300:
                    n_ok += 1
                else:
                    n_fail += 1
                    print(f"  ! HTTP {resp.status} {d['aspectName']} on {d['entityUrn']}", file=sys.stderr)
        except urllib.error.HTTPError as e:
            n_fail += 1
            payload = e.read()[:300]
            print(f"  ! {e.code} {d['aspectName']} on {d['entityUrn']}: {payload!r}", file=sys.stderr)
        except urllib.error.URLError as e:
            n_fail += 1
            print(f"  ! 연결 실패 {url}: {e}", file=sys.stderr)
            break
    return n_ok, n_fail


# ===== 모드 3: acryl-datahub SDK =====

def emit_via_acryl(mcps: Iterable[dict], gms_url: str, token: str | None) -> tuple[int, int]:
    from datahub.emitter.rest_emitter import DatahubRestEmitter
    from datahub.metadata.com.linkedin.pegasus2avro.mxe import (
        GenericAspect,
        MetadataChangeProposal,
    )
    emitter = DatahubRestEmitter(gms_server=gms_url, token=token or None)
    n_ok = n_fail = 0
    for d in mcps:
        try:
            aspect = GenericAspect(
                value=d["aspect"]["value"].encode("utf-8") if isinstance(d["aspect"]["value"], str) else d["aspect"]["value"],
                contentType=d["aspect"].get("contentType", "application/json"),
            )
            mcp = MetadataChangeProposal(
                entityType=d["entityType"],
                entityUrn=d["entityUrn"],
                changeType=d["changeType"],
                aspectName=d["aspectName"],
                aspect=aspect,
            )
            emitter.emit(mcp)
            n_ok += 1
        except Exception as e:  # noqa: BLE001
            n_fail += 1
            print(f"  ! emit 실패 {d['entityUrn']} {d['aspectName']}: {e}", file=sys.stderr)
    return n_ok, n_fail


# ===== CLI =====

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--jsonl", required=True, help="export 할 MCP JSONL 경로 (datahub_lite.export_mcps_jsonl 결과)")
    p.add_argument("--mode", choices=["frontend", "gms", "acryl"], default="frontend")

    # frontend 모드
    p.add_argument("--frontend", default="http://localhost:9002",
                   help="(frontend 모드) DataHub 프론트엔드 URL")
    p.add_argument("--user", default="datahub", help="(frontend 모드) username")
    p.add_argument("--password", default="datahub", help="(frontend 모드) password")

    # gms / acryl 모드
    p.add_argument("--gms-url", help="(gms/acryl 모드) GMS 베이스 URL (예: http://localhost:8080)")
    p.add_argument("--token", default="", help="(gms/acryl 모드) DataHub access token")

    args = p.parse_args()

    src = Path(args.jsonl)
    if not src.exists():
        sys.exit(f"입력 파일이 없습니다: {src}")

    mcps = []
    with src.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                mcps.append(json.loads(line))

    print(f"입력: {src} ({len(mcps)} 개 MCP)")
    print(f"모드: {args.mode}")

    if args.mode == "frontend":
        print(f"frontend: {args.frontend} (user={args.user})")
        n_ok, n_fail = emit_via_frontend(mcps, args.frontend, args.user, args.password)
    elif args.mode == "gms":
        if not args.gms_url:
            sys.exit("--gms-url 필요")
        print(f"GMS: {args.gms_url}")
        n_ok, n_fail = emit_via_gms(mcps, args.gms_url, args.token or None)
    elif args.mode == "acryl":
        if not args.gms_url:
            sys.exit("--gms-url 필요")
        print(f"GMS: {args.gms_url} (acryl-datahub SDK)")
        n_ok, n_fail = emit_via_acryl(mcps, args.gms_url, args.token or None)

    print(f"\n✓ 성공 {n_ok} / 실패 {n_fail}")
    if n_fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
