"""기본 사용 예제: 데이터셋 등록 → 태그/오너/계보 → JSONL/HTML 출력.

실행:
    python examples/basic_usage.py
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from datahub_lite import (
    LocalCatalog,
    make_dataset_urn,
    make_data_job_urn,
)


def main() -> None:
    out_dir = Path(__file__).resolve().parent.parent / "data"
    out_dir.mkdir(exist_ok=True)
    catalog_path = out_dir / "basic_catalog.jsonl"
    if catalog_path.exists():
        catalog_path.unlink()
    cat = LocalCatalog(catalog_path)

    # ---- 1) 원천 카프카 토픽 ----
    raw = make_dataset_urn("kafka", "raw_clicks")
    cat.upsert_dataset_properties(raw, description="Web click 이벤트 원천 (Kafka topic)")
    cat.upsert_schema_metadata(raw, [
        {"fieldPath": "user_id", "type": "long", "description": "사용자 ID"},
        {"fieldPath": "session_id", "type": "string", "description": "세션 ID"},
        {"fieldPath": "url", "type": "string", "description": "클릭한 URL"},
        {"fieldPath": "ts_ms", "type": "long", "description": "이벤트 타임스탬프(ms)"},
    ])
    cat.add_tag(raw, "Streaming")
    cat.add_owner(raw, "data-platform-team", owner_type="TECHNICAL_OWNER")

    # ---- 2) Hive 분석 테이블 ----
    sessions = make_dataset_urn("hive", "analytics.user_sessions")
    cat.upsert_dataset_properties(
        sessions,
        description="사용자별 세션 단위 집계 테이블",
        custom_properties={"partitioned_by": "dt", "format": "parquet"},
    )
    cat.upsert_schema_metadata(sessions, [
        {"fieldPath": "user_id", "type": "long"},
        {"fieldPath": "session_id", "type": "string"},
        {"fieldPath": "click_count", "type": "long"},
        {"fieldPath": "duration_sec", "type": "long"},
        {"fieldPath": "dt", "type": "date", "description": "파티션 키"},
    ])
    cat.add_owner(sessions, "analytics-team", owner_type="DATAOWNER")
    cat.set_domain(sessions, "Analytics")

    # ---- 3) 계보 ----
    cat.add_upstream_lineage(sessions, [raw])

    # ---- 4) 변환 작업 (Airflow DAG) ----
    job = make_data_job_urn("airflow", "user_sessions_dag", "build_sessions")
    cat.upsert(job, "dataJobInfo", {
        "name": "build_sessions",
        # type 은 Pegasus 의 union 타입. 키로 discriminator 클래스명을 사용.
        "type": {"com.linkedin.datajob.azkaban.AzkabanJobType": "COMMAND"},
        "description": "raw_clicks → user_sessions 일배치",
    })
    cat.upsert(job, "dataJobInputOutput", {
        "inputDatasets": [raw],
        "outputDatasets": [sessions],
    })

    # ---- 5) export ----
    cat.export_html(out_dir / "basic_catalog.html", title="기본 사용 예제")
    cat.export_mcps_jsonl(out_dir / "basic_mcps.jsonl")

    print("등록된 엔티티 통계:", cat.stats())
    print()
    print("검색 'session':")
    for r in cat.search("session"):
        print(f"  - {r['displayName']:40s}  {r['urn']}")
    print()
    print(f"카탈로그 (JSONL):     {catalog_path}")
    print(f"HTML 뷰어:            {out_dir / 'basic_catalog.html'}")
    print(f"DataHub-호환 MCP:     {out_dir / 'basic_mcps.jsonl'}")


if __name__ == "__main__":
    main()
