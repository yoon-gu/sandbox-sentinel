# 003-datahub-lite

> 폐쇄망에서 동작하는 single-file DataHub 호환 메타데이터 카탈로그 + LLM tool-calling 인터페이스

## 한 줄 요약
DataHub의 엔티티-aspect 모델(63 엔티티 / 226 aspect)을 그대로 차용하여, **표준 라이브러리만으로 로컬에서 카탈로그를 구축**하고 완성되면 **실제 DataHub로 그대로 import**할 수 있게 해주는 모듈입니다.

## 원본 출처
| 항목 | 값 |
|---|---|
| 라이브러리 | DataHub ([datahub-project/datahub](https://github.com/datahub-project/datahub)) |
| 호환 대상 버전 | 1.4.x |
| 라이선스 | Apache-2.0 ([LICENSE](LICENSE) 참고) |

## 왜 만들었나

DataHub은 강력하지만 폐쇄망 환경에서 다음이 어렵습니다:
1. **풀스택 인프라**(GMS + Elasticsearch + Kafka + MAE)가 무거워 PoC 단계에서 띄우기 어려움
2. `acryl-datahub` SDK가 사내 미러에 등록되어야 하는 등 **반입 절차 부담**
3. 메타데이터 **초안을 짜는 단계**에서는 곧장 실 DataHub에 쏘기보다 로컬에서 모델링하고 검증하고 싶음

이 모듈은 그 간극을 메웁니다:
- **본체(`datahub_lite.py`)는 외부 의존 0** — 단일 파일만 반입하면 동작
- DataHub의 **엔티티-aspect-URN 컨벤션을 100% 그대로** 사용 → 학습 비용 0
- 작업 결과는 **MCP JSONL** 한 파일로 export → 실 DataHub의 ingestProposal로 그대로 들어감
- 카탈로그 시각화는 **self-contained HTML** → 보안 심사 통과 가능 (외부 fetch 없음)
- LLM tool-calling 인터페이스 내장 → Agent 가 자연어로 카탈로그 조작

## 기능 요약

| 기능 | 위치 |
|---|---|
| URN 파서/빌더 (dataset/chart/dashboard/dataJob/tag/...) | `datahub_lite.make_*_urn`, `parse_urn` |
| 엔티티-aspect 인메모리 카탈로그 + JSONL 영속화 | `LocalCatalog` |
| 자주 쓰는 aspect 편의 메서드 | `upsert_dataset_properties`, `upsert_schema_metadata`, `add_tag`, `add_owner`, `set_domain`, `add_upstream_lineage`, `soft_delete` 등 |
| 임의 aspect upsert (모든 226 aspect 지원) | `LocalCatalog.upsert(urn, aspect_name, aspect_dict)` |
| 검색 (이름·설명·URN substring + entity_type/tags 필터) | `LocalCatalog.search` |
| 계보 조회 | `get_upstream_urns`, `get_downstream_urns` |
| MCP JSONL export (DataHub ingest 호환) | `LocalCatalog.export_mcps_jsonl` |
| self-contained HTML 카탈로그 | `LocalCatalog.export_html` |
| OpenAI/Anthropic LLM tool-calling | `TOOLS`, `dispatch_tool`, `tools_for_openai`, `tools_for_anthropic` |
| 실 DataHub로 export (3가지 모드) | `examples/export_to_datahub.py` |
| 실 DataHub에서 통째로 import (3가지 모드) | `examples/import_from_datahub.py` |

지원 엔티티 (63종): `application, assertion, businessAttribute, chart, container, corpGroup, corpuser, dashboard, dataContract, dataFlow, dataHub*, dataJob, dataPlatform, dataPlatformInstance, dataProcess, dataProcessInstance, dataProduct, dataType, dataset, document, domain, entityType, erModelRelationship, form, glossaryNode, glossaryTerm, incident, mlFeature, mlFeatureTable, mlModel, mlModelDeployment, mlModelGroup, mlPrimaryKey, notebook, notebook, ownershipType, platformResource, post, query, role, schemaField, structuredProperty, tag, test, versionSet, ...`

## 의존성

- **본체 `datahub_lite.py`**: Python 3.9+ 표준 라이브러리만
- **`examples/export_to_datahub.py`**: 표준 라이브러리만 (acryl 모드 사용 시 `acryl-datahub` 선택)
- **`registry/fetch_registry.py`**: 표준 라이브러리만 (실 DataHub에서 레지스트리를 갱신할 때만 사용)

## 사용 예시

### 1) 카탈로그 작성 (`examples/basic_usage.py`)

```python
from datahub_lite import LocalCatalog, make_dataset_urn

cat = LocalCatalog("./data/my_catalog.jsonl")

raw = make_dataset_urn("kafka", "raw_clicks")
cat.upsert_dataset_properties(raw, description="Web click 이벤트 원천")
cat.upsert_schema_metadata(raw, [
    {"fieldPath": "user_id", "type": "long"},
    {"fieldPath": "url", "type": "string"},
])
cat.add_tag(raw, "Streaming")
cat.add_owner(raw, "data-platform-team")

sessions = make_dataset_urn("hive", "analytics.user_sessions")
cat.add_upstream_lineage(sessions, [raw])

cat.export_html("./data/catalog.html")        # 브라우저로 열어 보기
cat.export_mcps_jsonl("./data/mcps.jsonl")    # 실 DataHub 로 import 할 페이로드
```

### 2) LLM tool-calling (`examples/tool_calling.py`)

```python
from datahub_lite import LocalCatalog, dispatch_tool, tools_for_anthropic

cat = LocalCatalog("./data/my_catalog.jsonl")

# Anthropic Messages API 에 그대로 넘김
tools = tools_for_anthropic()

# 모델이 반환한 tool_use 를 그대로 dispatch
result = dispatch_tool(cat, "register_dataset", {
    "platform": "snowflake",
    "name": "DW.SALES.FACT_REVENUE",
    "description": "일별 매출 합계",
    "fields": [{"fieldPath": "dt", "type": "date"}, {"fieldPath": "revenue_krw", "type": "decimal"}],
    "tags": ["Finance"],
})
```

내장 tool 목록: `search_entities, get_entity, get_aspect, upsert_aspect, register_dataset, tag_entity, add_owner, add_upstream_lineage, get_lineage, list_entities, stats, export_mcps, export_html`

### 3) 실 DataHub로 import (`examples/export_to_datahub.py`)

세 가지 모드를 지원합니다:

```bash
# A) frontend 모드 (가장 폐쇄망 친화적, stdlib만)
python examples/export_to_datahub.py \
    --jsonl data/basic_mcps.jsonl \
    --frontend http://localhost:9002 \
    --user datahub --password datahub

# B) GMS 직접 (token 인증, stdlib만)
python examples/export_to_datahub.py --mode gms \
    --jsonl data/basic_mcps.jsonl \
    --gms-url http://gms-host:8080 --token "$DH_TOKEN"

# C) acryl-datahub SDK 사용
python examples/export_to_datahub.py --mode acryl \
    --jsonl data/basic_mcps.jsonl \
    --gms-url http://gms-host:8080 --token "$DH_TOKEN"
```

### 4) 실 DataHub에서 import (`examples/import_from_datahub.py`)

`export_to_datahub.py` 의 역방향 — DataHub에 이미 쌓여 있는 메타데이터를 통째로 로컬 JSONL로 가져와 폐쇄망에서 편집/탐색합니다.

```bash
# 전체 사용자 엔티티를 통째로 (DataHub 운영 엔티티는 자동 제외)
python examples/import_from_datahub.py \
    --out data/imported.jsonl \
    --frontend http://localhost:9002 \
    --user datahub --password datahub

# DataHub 운영용 엔티티(telemetry, dataHubExecutionRequest 등)까지 포함
python examples/import_from_datahub.py \
    --out data/full.jsonl --include-internal \
    --frontend http://localhost:9002 --user datahub --password datahub

# 검색어로 부분만 가져오기
python examples/import_from_datahub.py \
    --out data/sales.jsonl --query "sales" \
    --frontend http://localhost:9002 --user datahub --password datahub

# GMS / acryl 모드
python examples/import_from_datahub.py --mode gms \
    --out data/from_prod.jsonl \
    --gms-url http://gms-host:8080 --token "$DH_TOKEN"
```

**동작**: GraphQL `scrollAcrossEntities` 로 모든 URN 페이지네이션 → URN 마다 `/api/gms/entitiesV2/{urn}` GET 1회로 모든 aspect 획득 → `LocalCatalog.upsert()` 로 저장. 기준 인스턴스 568 entities × 3387 aspects 가 7~8초 안에 import.

가져온 카탈로그는 `LocalCatalog` 의 모든 기능(검색, 계보, HTML, LLM tool calling, 다른 DataHub 로 재-export)을 그대로 사용할 수 있습니다.

### 5) DataHub 레지스트리 갱신 (`registry/fetch_registry.py`)

DataHub이 새 엔티티/aspect를 추가했을 때 `datahub_lite.py`의 `ENTITY_REGISTRY` 블록을 갱신:

```bash
python registry/fetch_registry.py \
    --frontend http://localhost:9002 \
    --user datahub --password datahub \
    --out registry/registry_data.py

# 출력된 registry_data.py 내용을 datahub_lite.py 의 동명 블록에 붙여넣기
```

## 영속화 정책

CLAUDE.md의 self-contained HTML 원칙을 준수합니다:
- **JSONL** (텍스트, append-only): 카탈로그 상태 — 사람이 diff/grep 가능
- **HTML** (self-contained, 외부 fetch 없음): 시각화·공유용 — 데이터·CSS·JS 모두 인라인
- **MCP JSONL**: DataHub ingest용 텍스트 페이로드

바이너리 영속화(.pkl/.db/.parquet/...)는 사용하지 않습니다.

## 아키텍처 노트

```
LocalCatalog (메모리)
  ├─ _aspects: {urn: {aspect_name: aspect_dict}}      ← 인메모리 dict
  ├─ _types:   {urn: entity_type}                      ← URN→타입 캐시
  └─ _changes: list[AspectChange]                      ← 세션 내 변경 로그

  ↓ persist (autosave=True)
  JSONL append-only log (사람이 읽고 grep 가능)

  ↓ export_mcps_jsonl
  MCP JSONL (DataHub ingestProposal 호환 — 실 DataHub로 그대로 들어감)

  ↓ export_html
  self-contained HTML (보안 심사 통과 가능)
```

## 알려진 제약/한계점

- DataHub의 **엄격한 Pegasus 스키마 검증은 수행하지 않습니다**. 잘못된 aspect 모양은 export 단계에서 DataHub가 거부할 수 있습니다 (실제 본 모듈로 작성한 카탈로그는 round-trip 검증되어 있음).
- 일부 aspect의 **union 타입은 사용자가 discriminator 키를 명시**해야 합니다. 예:
  - `dataJobInfo.type` → `{"com.linkedin.datajob.azkaban.AzkabanJobType": "COMMAND"}`
  - `schemaMetadata.platformSchema` → 편의 메서드는 `Schemaless`로 자동 채움
- **timeseries aspect**(`datasetProfile`, `operation`, `*UsageStatistics` 등 10종)는 시계열로 저장되지만, 본 모듈은 같은 `(urn, aspectName)` 쌍을 단일 값으로 덮어씁니다.
- **검색은 단순 substring** 기반. Elasticsearch 풍의 풀텍스트/팟셋 검색은 없음.
- **엔티티 레지스트리는 DataHub 1.4.x 시점 snapshot**. 사내 DataHub 버전이 다르면 `registry/fetch_registry.py`로 갱신.

## 폴더 구조

```
003-datahub-lite/
├── README.md                    # 이 문서
├── datahub_lite.py              # 본체 single-file (외부 의존 0)
├── metadata.json                # 변환 메타데이터
├── LICENSE                      # Apache-2.0 (DataHub 원본)
├── examples/
│   ├── basic_usage.py           # 카탈로그 작성 + JSONL/HTML 출력
│   ├── tool_calling.py          # LLM tool-calling 인터페이스 데모
│   ├── export_to_datahub.py     # 실 DataHub로 ingest (3가지 모드)
│   └── import_from_datahub.py   # 실 DataHub의 모든 엔티티/aspect 를 통째로 import
├── registry/
│   └── fetch_registry.py        # DataHub에서 엔티티/aspect 레지스트리 재추출
└── data/                        # 데모 출력 (JSONL/MCP/HTML, gitignore 권장)
```
