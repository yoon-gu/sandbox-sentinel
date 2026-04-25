"""
006-sql-runner-notebook · basic usage

스크립트 단독 실행 시 ipywidgets 위젯은 그릴 수 없으므로 SQLRunner 의
스키마 등록 + highlight_sql_html / get_suggestions 등 핵심 함수를 호출해
정상 동작을 확인합니다. 실제 위젯은 Jupyter 셀에서 다음과 같이 사용:

    import sqlite3, pandas as pd
    from sql_runner import SQLRunner

    conn = sqlite3.connect("./local.db")
    runner = SQLRunner(on_execute=lambda sql: pd.read_sql(sql, conn))
    runner.from_sqlite("./local.db")
    runner.set_query("SELECT * FROM users LIMIT 10;")
    runner.show()
"""
from __future__ import annotations

import os
import sys

# 상위 디렉토리의 sql_runner.py import
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sql_runner import (   # noqa: E402
    SQLRunner,
    highlight_sql_html,
    detect_context,
    get_suggestions,
)


def build_demo_runner() -> SQLRunner:
    runner = SQLRunner()
    runner.add_table("users", [
        ("id", "INTEGER", "PK"),
        ("name", "TEXT"),
        ("email", "TEXT"),
    ], description="사용자 마스터")
    runner.add_table("orders", [
        ("id", "INTEGER", "PK"),
        ("user_id", "INTEGER", "FK → users.id"),
        ("amount", "REAL"),
        ("status", "TEXT"),
    ], description="주문 트랜잭션")
    runner.set_query(
        "-- 사용자별 결제 합계\n"
        "SELECT u.name, SUM(o.amount) AS total\n"
        "FROM users u\n"
        "JOIN orders o ON o.user_id = u.id\n"
        "WHERE o.status = 'paid'\n"
        "GROUP BY u.name\n"
        "ORDER BY total DESC;"
    )
    return runner


if __name__ == "__main__":
    runner = build_demo_runner()
    print("=== highlight_sql_html(initial_query) 일부 ===")
    html = highlight_sql_html(runner.initial_query)
    print(html[:240], "...\n(총", len(html), "chars)\n")

    print("=== detect_context 테스트 ===")
    for q in [
        "",
        "SELECT ",
        "SELECT * FROM ",
        "SELECT id FROM users WHERE ",
        "SELECT id FROM users WHERE u.",
        "SELECT id FROM users GROUP BY ",
        "DELETE ",
    ]:
        print(f"  {q!r:40}  →  {detect_context(q)!r}")

    print()
    print("=== get_suggestions 예시 ('SELECT * FROM ' 다음) ===")
    sugs = get_suggestions("SELECT * FROM ", runner.tables)
    for s in sugs[:8]:
        print(f"  {s['kind']:10} {s['label']:20} ({s.get('meta', '')})")

    print()
    print("✓ 핵심 함수 동작 확인 완료. 실제 위젯은 Jupyter 셀에서 .show() 호출")
