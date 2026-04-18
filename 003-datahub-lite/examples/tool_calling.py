"""LLM tool-calling 인터페이스 예제.

이 스크립트는 LLM 없이도 동작하도록 "사용자가 LLM 인 척" 하는 시뮬레이션 모드를 제공합니다.
실제 환경에서는 OpenAI/Anthropic SDK 의 tools 파라미터에
`tools_for_openai()` / `tools_for_anthropic()` 결과를 그대로 넘기고,
모델이 반환한 tool_use 를 `dispatch_tool(catalog, name, args)` 로 실행하면 됩니다.

실행:
    python examples/tool_calling.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from datahub_lite import (
    LocalCatalog,
    TOOLS,
    dispatch_tool,
    tools_for_anthropic,
    tools_for_openai,
)


def simulate_llm_session(catalog: LocalCatalog) -> None:
    """LLM 이 호출했을 법한 일련의 tool call 시퀀스를 흉내냅니다."""

    sequence = [
        # 1) 새 dataset 등록
        ("register_dataset", {
            "platform": "snowflake",
            "name": "DW.SALES.FACT_REVENUE",
            "description": "일별 매출 합계 (KRW)",
            "fields": [
                {"fieldPath": "dt", "type": "date"},
                {"fieldPath": "channel", "type": "string"},
                {"fieldPath": "revenue_krw", "type": "decimal"},
            ],
            "tags": ["Finance", "Daily"],
            "owners": ["finance-team"],
        }),
        # 2) 다른 dataset 추가
        ("register_dataset", {
            "platform": "snowflake",
            "name": "DW.SALES.STG_TRANSACTIONS",
            "description": "스테이징 트랜잭션 (raw)",
            "fields": [
                {"fieldPath": "txn_id", "type": "string"},
                {"fieldPath": "amount", "type": "decimal"},
                {"fieldPath": "ts", "type": "timestamp"},
            ],
        }),
        # 3) 계보 추가
        ("add_upstream_lineage", {
            "downstream_urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,DW.SALES.FACT_REVENUE,PROD)",
            "upstream_urns": ["urn:li:dataset:(urn:li:dataPlatform:snowflake,DW.SALES.STG_TRANSACTIONS,PROD)"],
        }),
        # 4) 검색
        ("search_entities", {"query": "revenue", "limit": 5}),
        # 5) 단일 aspect 조회
        ("get_aspect", {
            "entity_urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,DW.SALES.FACT_REVENUE,PROD)",
            "aspect_name": "datasetProperties",
        }),
        # 6) 통계
        ("stats", {}),
    ]

    for name, args in sequence:
        print(f"\n>>> tool: {name}({json.dumps(args, ensure_ascii=False)[:120]})")
        result = dispatch_tool(catalog, name, args)
        print(json.dumps(result, indent=2, ensure_ascii=False)[:600])


def main() -> None:
    out_dir = Path(__file__).resolve().parent.parent / "data"
    out_dir.mkdir(exist_ok=True)
    cat = LocalCatalog(out_dir / "tool_calling.jsonl")

    print("=== 사용 가능한 tools ===")
    for t in TOOLS:
        print(f"  • {t['name']:25s} {t['description'][:60]}")

    print("\n=== OpenAI 호환 스키마 (head 1) ===")
    print(json.dumps(tools_for_openai()[0], indent=2, ensure_ascii=False))

    print("\n=== Anthropic 호환 스키마 (head 1) ===")
    print(json.dumps(tools_for_anthropic()[0], indent=2, ensure_ascii=False))

    print("\n=== LLM 세션 시뮬레이션 ===")
    simulate_llm_session(cat)

    cat.export_html(out_dir / "tool_calling_catalog.html", title="Tool Calling 데모")
    print(f"\n✓ HTML: {out_dir / 'tool_calling_catalog.html'}")


if __name__ == "__main__":
    main()
