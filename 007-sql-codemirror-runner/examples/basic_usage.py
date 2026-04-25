"""007-sql-codemirror-runner — CLI 기본 동작 검증.

Jupyter 없이도 실행 가능 — 임포트, 스키마 등록, 부트스트랩 HTML 생성까지를
단위 검증한다. 실제 에디터 UI 는 demo.ipynb 또는 별도 노트북 셀에서 확인.
"""
from __future__ import annotations

import os
import sqlite3
import sys

# repo root 의 폴더 import 경로 보정 (examples/ 에서 직접 실행 시)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from sql_codemirror import SQLRunnerCM, _CM_JS, _CM_SQL_JS  # noqa: E402


def make_demo_db(path: str = "/tmp/sql_codemirror_demo.db") -> str:
    """간단한 데모 sqlite DB 생성 (idempotent)."""
    if os.path.exists(path):
        os.remove(path)
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE users (
                id    INTEGER PRIMARY KEY,
                name  TEXT NOT NULL,
                email TEXT
            );
            CREATE TABLE orders (
                id      INTEGER PRIMARY KEY,
                user_id INTEGER REFERENCES users(id),
                amount  REAL,
                status  TEXT
            );
            INSERT INTO users(name, email) VALUES
                ('Alice', 'alice@example.com'),
                ('Bob',   'bob@example.com'),
                ('Carol', 'carol@example.com');
            INSERT INTO orders(user_id, amount, status) VALUES
                (1, 19.50, 'paid'),
                (2,  5.00, 'paid'),
                (3, 99.99, 'pending');
            """
        )
    return path


def main() -> None:
    print("=" * 60)
    print("[1] 인라인 CM 번들 크기")
    print("=" * 60)
    print(f"  codemirror.min.js : {len(_CM_JS):>7,} bytes")
    print(f"  sql.min.js        : {len(_CM_SQL_JS):>7,} bytes")
    print()

    print("=" * 60)
    print("[2] 스키마 등록 — add_table / from_dict")
    print("=" * 60)
    r1 = (
        SQLRunnerCM()
        .add_table("users", ["id", "name", "email"], description="사용자 마스터")
        .add_table("orders", [
            ("id", "INT"), ("user_id", "INT"),
            ("amount", "REAL"), ("status", "TEXT"),
        ])
    )
    print(f"  tables: {list(r1.tables.keys())}")
    print(f"  orders cols: {[c['name'] for c in r1.tables['orders']]}")
    print()

    print("=" * 60)
    print("[3] from_sqlite — 실제 DB 에서 스키마 자동 추출")
    print("=" * 60)
    db = make_demo_db()
    r2 = SQLRunnerCM().from_sqlite(db)
    for t, cols in r2.tables.items():
        types = [(c["name"], c["type"]) for c in cols]
        print(f"  {t}: {types}")
    print()

    print("=" * 60)
    print("[4] with_sqlite (편의 생성자) → on_execute 콜백 자동 주입")
    print("=" * 60)
    r3 = SQLRunnerCM.with_sqlite(db)
    r3.set_query("SELECT u.name, COUNT(o.id) AS n_orders\n"
                 "FROM users u LEFT JOIN orders o ON u.id = o.user_id\n"
                 "GROUP BY u.id ORDER BY n_orders DESC;")
    print(f"  on_execute set?  {r3.on_execute is not None}")
    print(f"  initial_query (first line): {r3.initial_query.splitlines()[0]}")
    print()

    print("=" * 60)
    print("[5] 부트스트랩 HTML — CM 인스턴스화 JS 가 정상 생성되는지")
    print("=" * 60)
    boot = r3._cm_bootstrap_html()
    bundle = r3._cm_bundle_html()
    print(f"  bootstrap js bytes: {len(boot):>7,}")
    print(f"  CM CSS+JS bundle bytes: {len(bundle):>7,}")
    assert "CodeMirror" in bundle, "CM JS 가 번들에 포함되어야 함"
    assert "x-sql" in boot, "SQL mode 활성화 코드가 부트스트랩에 있어야 함"
    assert "contextHint" in boot, "컨텍스트 힌트 함수가 부트스트랩에 있어야 함"
    assert r3._uid in boot, "uid 가 placeholder 치환되어야 함"
    print("  → 모든 검증 통과")
    print()

    print("=" * 60)
    print("[6] on_execute 콜백 직접 호출 (Jupyter 우회)")
    print("=" * 60)
    rows = r3.on_execute("SELECT name, email FROM users ORDER BY id LIMIT 3;")
    print(rows)
    print()

    print("✓ 모든 단위 검증 통과 — Jupyter 셀에서 runner.show() 로 UI 확인.")


if __name__ == "__main__":
    main()
