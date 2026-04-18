"""실제 DataHub 인스턴스의 모든 엔티티/aspect 를 로컬 카탈로그(JSONL) 로 import.

`export_to_datahub.py` 의 역방향 — DataHub 에 이미 쌓여 있는 메타데이터를 폐쇄망 로컬에서
편집/탐색하기 위해 통째로 끌어옵니다.

동작 흐름
--------
  1) GraphQL `scrollAcrossEntities` 로 모든 엔티티 URN 을 페이지 단위로 enumerate
  2) URN 마다 `/api/gms/entitiesV2/{urn}` GET — 한 번 호출에 모든 aspect 가 포함됨
  3) 각 aspect 의 `value` 만 추출해 `LocalCatalog.upsert()` 로 저장
  4) 결과는 지정한 JSONL 경로에 누적 — 끝나면 export_html / export_mcps_jsonl 로 활용 가능

지원 모드
--------
  - **frontend** (기본): :9002 + cookie. stdlib 만 사용.
  - **gms**: GMS URL(:8080) + Bearer token. stdlib 만 사용.
  - **acryl**: `acryl-datahub` SDK 의 DataHubGraph 사용 (별도 검증/타입체크).

사용 예
------
  # 1) frontend 모드 — 가장 단순
  python examples/import_from_datahub.py \\
      --out data/imported.jsonl \\
      --frontend http://localhost:9002 --user datahub --password datahub

  # 2) DataHub 내부 엔티티(telemetry, dataHubExecutionRequest 등) 까지 포함
  python examples/import_from_datahub.py \\
      --out data/full.jsonl --include-internal \\
      --frontend http://localhost:9002 --user datahub --password datahub

  # 3) 검색어 필터 — query="*" 가 기본. 특정 키워드만 가져오려면:
  python examples/import_from_datahub.py \\
      --out data/sales.jsonl --query "sales" \\
      --frontend http://localhost:9002 --user datahub --password datahub

  # 4) 가져온 결과 활용
  python -c "
  import sys; sys.path.insert(0, '.')
  from datahub_lite import LocalCatalog
  cat = LocalCatalog('data/imported.jsonl')
  print(cat.stats())
  cat.export_html('data/imported.html')"
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterator, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from datahub_lite import ENTITY_REGISTRY, LocalCatalog, parse_urn

# DataHub 자체 운영용 엔티티 — 보통은 사용자 카탈로그 관심 밖. --include-internal 로 포함 가능.
_INTERNAL_ENTITY_TYPES: frozenset[str] = frozenset({
    "telemetry", "inviteToken",
    "dataHubAccessToken", "dataHubAction", "dataHubConnection",
    "dataHubExecutionRequest", "dataHubFile", "dataHubIngestionSource",
    "dataHubOpenAPISchema", "dataHubPageModule", "dataHubPageTemplate",
    "dataHubPersona", "dataHubPolicy", "dataHubRetention", "dataHubRole",
    "dataHubSecret", "dataHubStepState", "dataHubUpgrade", "dataHubView",
    "globalSettings",
})

# entitiesV2 응답에서 vacuum 해야 할 메타필드 (로컬 카탈로그는 aspect 페이로드 본체만 보관)
_ENVELOPE_KEYS: frozenset[str] = frozenset({"created", "lastModified", "systemMetadata", "name", "type", "version"})


# ===== HTTP 클라이언트 (stdlib) =====

class _DataHubHTTP:
    """frontend(cookie) / gms(token) 두 모드를 동일 인터페이스로 감쌈."""

    def __init__(self, base_url: str, *, token: Optional[str] = None,
                 user: Optional[str] = None, password: Optional[str] = None,
                 mode: str = "frontend"):
        self.base_url = base_url.rstrip("/")
        self.mode = mode
        self.token = token
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        if mode == "frontend":
            assert user and password, "frontend 모드는 user/password 필요"
            self._login_frontend(user, password)
        # gms 모드는 매 요청에 Bearer 헤더만 붙이면 됨

    def _login_frontend(self, user: str, password: str) -> None:
        body = json.dumps({"username": user, "password": password}).encode("utf-8")
        req = urllib.request.Request(f"{self.base_url}/logIn", data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with self._opener.open(req, timeout=15) as resp:
            if resp.status != 200:
                raise SystemExit(f"frontend 로그인 실패: HTTP {resp.status}")

    def _headers(self, extra: Optional[dict] = None) -> dict:
        h = {"Content-Type": "application/json"}
        if self.mode == "gms" and self.token:
            h["Authorization"] = f"Bearer {self.token}"
        if extra:
            h.update(extra)
        return h

    def graphql(self, query: str, variables: Optional[dict] = None) -> dict:
        url = self._gql_url()
        body = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=self._headers(), method="POST")
        with self._opener.open(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def get_entity_v2(self, urn: str) -> Optional[dict]:
        """`/api/gms/entitiesV2/{urn}` (frontend) 또는 `/entitiesV2/{urn}` (gms) GET.

        반환: `{urn, entityName, aspects: {<aspectName>: {value, ...envelope}}}` 또는 None.
        """
        encoded = urllib.parse.quote(urn, safe="")
        url = f"{self._gms_root()}/entitiesV2/{encoded}"
        req = urllib.request.Request(url, headers=self._headers(), method="GET")
        try:
            with self._opener.open(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise

    def _gql_url(self) -> str:
        return f"{self.base_url}/api/graphql" if self.mode == "frontend" else f"{self.base_url}/api/graphql"

    def _gms_root(self) -> str:
        return f"{self.base_url}/api/gms" if self.mode == "frontend" else self.base_url


# ===== 엔티티 enumerate =====

_GQL_SCROLL = """
query Scroll($input: ScrollAcrossEntitiesInput!) {
  scrollAcrossEntities(input: $input) {
    nextScrollId
    count
    total
    searchResults { entity { urn type } }
  }
}
"""


def iter_all_urns(http: _DataHubHTTP, query: str = "*", page_size: int = 1000,
                  include_soft_deleted: bool = True) -> Iterator[tuple[str, str, int, int]]:
    """모든 URN 을 (urn, entity_type_uppercase, page_idx, total) 로 yield.

    DataHub 의 GraphQL `scrollAcrossEntities` 는 nextScrollId 기반 페이지네이션을 사용.
    soft-deleted 엔티티도 포함하려면 SearchFlags 로 명시.
    """
    scroll_id: Optional[str] = None
    page = 0
    while True:
        page += 1
        variables = {
            "input": {
                "query": query,
                "count": int(page_size),
                "scrollId": scroll_id,
                "searchFlags": {"includeSoftDeleted": bool(include_soft_deleted)},
            }
        }
        resp = http.graphql(_GQL_SCROLL, variables)
        data = (resp.get("data") or {}).get("scrollAcrossEntities")
        if not data:
            errs = resp.get("errors")
            raise RuntimeError(f"scrollAcrossEntities 실패: {errs}")
        total = int(data.get("total") or 0)
        for r in data.get("searchResults") or []:
            ent = r.get("entity") or {}
            urn = ent.get("urn")
            etype = ent.get("type") or ""
            if urn:
                yield urn, etype, page, total
        scroll_id = data.get("nextScrollId")
        if not scroll_id or not data.get("searchResults"):
            break


# ===== 변환: entitiesV2 응답 → LocalCatalog 업서트 =====

def _strip_value(aspect_envelope: dict) -> Optional[dict]:
    """`{value: {...}, created, systemMetadata, ...}` 에서 value 만 빼고 None 정리."""
    if not isinstance(aspect_envelope, dict):
        return None
    val = aspect_envelope.get("value")
    return val if isinstance(val, dict) else None


def import_entity(catalog: LocalCatalog, http: _DataHubHTTP, urn: str) -> int:
    """단일 엔티티의 모든 aspect 를 로컬 카탈로그에 upsert. 적용된 aspect 수 반환."""
    env = http.get_entity_v2(urn)
    if not env or "aspects" not in env:
        return 0
    n = 0
    for aspect_name, wrapper in env["aspects"].items():
        value = _strip_value(wrapper)
        if value is None:
            continue
        try:
            catalog.upsert(urn, aspect_name, value)
            n += 1
        except Exception as e:  # noqa: BLE001
            print(f"  ! upsert 실패 {urn}.{aspect_name}: {e}", file=sys.stderr)
    return n


# ===== acryl 모드 =====

def run_acryl_mode(out_path: Path, gms_url: str, token: Optional[str],
                   query: str, include_internal: bool) -> tuple[int, int]:
    """acryl-datahub SDK 의 DataHubGraph 사용. 미설치 시 ImportError."""
    from datahub.ingestion.graph.client import DataHubGraph, DataHubGraphConfig

    graph = DataHubGraph(DataHubGraphConfig(server=gms_url, token=token or None))
    catalog = LocalCatalog(out_path)
    n_ent = n_asp = 0
    for urn in graph.get_urns_by_filter(query=query):
        etype, _ = parse_urn(urn)
        if not include_internal and etype in _INTERNAL_ENTITY_TYPES:
            continue
        aspects = graph.get_entity_raw(entity_urn=urn) or {}
        # acryl 의 raw 응답도 entitiesV2 envelope 모양이라 같은 strip 가능
        for aspect_name, wrapper in (aspects.get("aspects") or {}).items():
            value = _strip_value(wrapper)
            if value is not None:
                catalog.upsert(urn, aspect_name, value)
                n_asp += 1
        n_ent += 1
        if n_ent % 50 == 0:
            print(f"  ... {n_ent} entities", file=sys.stderr)
    return n_ent, n_asp


# ===== CLI =====

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", required=True, help="저장할 JSONL 경로 (기존 파일은 덮어쓰지 않고 append)")
    p.add_argument("--mode", choices=["frontend", "gms", "acryl"], default="frontend")

    # frontend
    p.add_argument("--frontend", default="http://localhost:9002")
    p.add_argument("--user", default="datahub")
    p.add_argument("--password", default="datahub")
    # gms / acryl
    p.add_argument("--gms-url", help="(gms/acryl 모드) GMS 베이스 URL (예: http://localhost:8080)")
    p.add_argument("--token", default="", help="(gms/acryl 모드) DataHub access token")
    # 공통
    p.add_argument("--query", default="*",
                   help="검색 키워드 (`*` = 전체). DataHub 의 search 문법을 그대로 사용.")
    p.add_argument("--page-size", type=int, default=1000)
    p.add_argument("--include-internal", action="store_true",
                   help="DataHub 운영 엔티티(telemetry, dataHubExecutionRequest 등)도 포함")
    p.add_argument("--include-soft-deleted", action="store_true", default=True,
                   help="status.removed=True 인 엔티티도 포함 (기본 True)")
    p.add_argument("--no-soft-deleted", dest="include_soft_deleted", action="store_false")

    args = p.parse_args()

    out_path = Path(args.out)
    if out_path.exists():
        print(f"기존 파일 발견 — 덮어씀: {out_path}", file=sys.stderr)
        out_path.unlink()

    print(f"모드: {args.mode}")
    print(f"출력: {out_path}")
    print(f"쿼리: {args.query!r}")
    print(f"내부 엔티티 포함: {args.include_internal}")
    print(f"soft-deleted 포함: {args.include_soft_deleted}")
    print()

    started = time.time()

    if args.mode == "acryl":
        if not args.gms_url:
            sys.exit("--gms-url 필요")
        n_ent, n_asp = run_acryl_mode(out_path, args.gms_url, args.token or None,
                                       args.query, args.include_internal)
    else:
        if args.mode == "frontend":
            http = _DataHubHTTP(args.frontend, mode="frontend",
                                user=args.user, password=args.password)
            print(f"frontend: {args.frontend} (user={args.user})")
        else:
            if not args.gms_url:
                sys.exit("--gms-url 필요")
            http = _DataHubHTTP(args.gms_url, mode="gms", token=args.token or None)
            print(f"GMS: {args.gms_url}")

        catalog = LocalCatalog(out_path)
        seen: set[str] = set()
        n_ent = n_asp = 0
        last_log = time.time()

        for urn, etype_upper, page, total in iter_all_urns(
            http, query=args.query, page_size=args.page_size,
            include_soft_deleted=args.include_soft_deleted,
        ):
            if urn in seen:
                continue
            seen.add(urn)

            etype, _ = parse_urn(urn)
            if not args.include_internal and etype in _INTERNAL_ENTITY_TYPES:
                continue

            n = import_entity(catalog, http, urn)
            if n:
                n_ent += 1
                n_asp += n

            now = time.time()
            if now - last_log >= 2.0:
                pct = (len(seen) * 100.0 / total) if total else 0.0
                print(f"  진행: {len(seen)}/{total or '?'} URN ({pct:.1f}%) · "
                      f"카탈로그 {n_ent} entities / {n_asp} aspects",
                      file=sys.stderr)
                last_log = now

        print(f"\n전체 enumerate URN: {len(seen)}", file=sys.stderr)

    elapsed = time.time() - started
    print(f"\n✓ 완료 — {n_ent} entities, {n_asp} aspects 를 {elapsed:.1f}s 만에 import")
    print(f"  파일: {out_path}")
    print(f"  활용: from datahub_lite import LocalCatalog; LocalCatalog({str(out_path)!r}).export_html(...)")


if __name__ == "__main__":
    main()
