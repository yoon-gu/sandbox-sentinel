"""
005-sql-editor-notebook · basic usage

스크립트 실행 시 self-contained HTML 파일을 만들어 브라우저로 직접 열어볼 수
있게 한다 (Jupyter 가 없는 환경에서도 동작 확인 가능). Jupyter 셀에서는
다음과 같이 사용:

    from sql_editor import SQLEditor
    editor = SQLEditor()
    editor.from_dict({
        "users": ["id", "name", "email"],
        "orders": ["id", "user_id", "amount", "status"],
    })
    editor.show()
"""
from __future__ import annotations

import os
import sys

# 상위 디렉토리의 sql_editor.py import (로컬 실행용)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sql_editor import SQLEditor  # noqa: E402


def build_demo_editor() -> SQLEditor:
    """샘플 스키마 — 사용자/주문/상품 (작은 ERD)."""
    editor = SQLEditor()
    editor.add_table(
        "users",
        [
            ("id", "INTEGER", "PK"),
            ("name", "TEXT"),
            ("email", "TEXT"),
            ("created_at", "DATETIME"),
        ],
        description="사용자 마스터",
    )
    editor.add_table(
        "products",
        [
            ("id", "INTEGER", "PK"),
            ("sku", "TEXT"),
            ("name", "TEXT"),
            ("price", "REAL"),
            ("category", "TEXT"),
        ],
        description="상품 카탈로그",
    )
    editor.add_table(
        "orders",
        [
            ("id", "INTEGER", "PK"),
            ("user_id", "INTEGER", "FK → users.id"),
            ("product_id", "INTEGER", "FK → products.id"),
            ("amount", "REAL"),
            ("status", "TEXT", "pending|paid|shipped|cancelled"),
            ("ordered_at", "DATETIME"),
        ],
        description="주문 트랜잭션",
    )
    editor.set_query(
        "-- 샘플 쿼리: 사용자별 결제 합계\n"
        "SELECT u.name, SUM(o.amount) AS total\n"
        "FROM users u\n"
        "JOIN orders o ON o.user_id = u.id\n"
        "WHERE o.status = 'paid'\n"
        "GROUP BY u.name\n"
        "ORDER BY total DESC\n"
        "LIMIT 10;"
    )
    return editor


if __name__ == "__main__":
    editor = build_demo_editor()
    out_path = os.path.abspath("sql_editor_demo.html")
    editor.save_html(out_path)
    print(f"✓ HTML 파일로 저장됨: {out_path}")
    print("  브라우저로 열어서 동작 확인 가능 (Jupyter 셀에서는 editor.show() 사용)")
