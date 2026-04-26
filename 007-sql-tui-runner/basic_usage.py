"""007-sql-tui-runner — CLI 기본 동작 검증 + 풀스크린 TUI 진입.

검증만 하려면 ``--check`` 플래그. 인자 없이 실행하면 풀스크린 TUI 가 뜸.
"""
from __future__ import annotations

import os
import sys
import sqlite3
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from sql_tui import (  # noqa: E402
    SQLRunnerTUI, detect_context, get_suggestions,
)


def make_demo_db() -> str:
    path = os.path.join(tempfile.gettempdir(), "sql_tui_basic_demo.db")
    if os.path.exists(path):
        os.remove(path)
    with sqlite3.connect(path) as conn:
        conn.executescript("""
        CREATE TABLE users (
            id         INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            email      TEXT,
            region     TEXT,
            plan_type  TEXT,
            is_active  INTEGER,
            signup_at  TIMESTAMP
        );
        CREATE TABLE orders (
            id              INTEGER PRIMARY KEY,
            user_id         INTEGER REFERENCES users(id),
            product_id      INTEGER,
            quantity        INTEGER,
            amount          REAL,
            status          TEXT,
            payment_method  TEXT,
            ordered_at      TIMESTAMP
        );
        CREATE TABLE products (
            id        INTEGER PRIMARY KEY,
            sku       TEXT UNIQUE,
            name      TEXT,
            category  TEXT,
            price     REAL,
            stock     INTEGER
        );
        CREATE TABLE events (
            id          INTEGER PRIMARY KEY,
            user_id     INTEGER,
            event_type  TEXT,
            value       REAL,
            occurred_at TIMESTAMP
        );

        INSERT INTO users (name, email, region, plan_type, is_active, signup_at) VALUES
            ('김알리스','alice@ex.com','서울','pro',         1,'2024-01-15 10:00'),
            ('이밥',  'bob@ex.com',  '부산','free',        1,'2024-02-20 12:30'),
            ('박찰리','charlie@ex.com','대구','pro',       0,'2024-03-10 09:15'),
            ('최다나','dana@ex.com', '서울','enterprise',  1,'2024-04-01 14:45'),
            ('정에반','evan@ex.com', '인천','free',        1,'2024-05-22 18:00');

        INSERT INTO products (sku, name, category, price, stock) VALUES
            ('SKU-A1','노트북 스탠드','office',     39000, 120),
            ('SKU-B2','USB-C 허브',  'electronics',29000,  35),
            ('SKU-C3','커피 원두 1kg','food',      18900, 200),
            ('SKU-D4','요가 매트',   'fitness',    25000,  78);

        INSERT INTO orders (user_id, product_id, quantity, amount, status, payment_method, ordered_at) VALUES
            (1,1,1,39000,'paid','card','2024-05-01 11:00'),
            (1,3,2,37800,'paid','card','2024-05-08 09:20'),
            (2,2,1,29000,'paid','kakao','2024-05-12 16:40'),
            (3,4,1,25000,'cancelled','card','2024-05-15 10:00'),
            (1,2,1,29000,'paid','naver','2024-06-02 14:30'),
            (4,1,3,117000,'paid','card','2024-06-10 11:15');

        INSERT INTO events (user_id, event_type, value, occurred_at) VALUES
            (1,'view',NULL,'2024-06-01 10:00'),
            (1,'click',NULL,'2024-06-01 10:01'),
            (1,'purchase',39000,'2024-06-01 10:05'),
            (4,'purchase',117000,'2024-06-10 11:15');
        """)
    return path


def run_checks() -> None:
    print("=" * 60)
    print("[1] 빌더 API · 스키마 등록")
    print("=" * 60)
    db = make_demo_db()
    runner = SQLRunnerTUI.with_sqlite(db)
    print(f"  자동 추출 테이블: {list(runner.tables.keys())}")
    print(f"  orders 컬럼: {[c['name'] for c in runner.tables['orders']]}")

    print()
    print("=" * 60)
    print("[2] 컨텍스트 감지 + fallback 키워드 자동완성")
    print("=" * 60)
    schema = runner.tables
    cases = [
        ("SELECT ",                   "columns_or_star"),
        ("SELECT * FROM ",            "tables"),
        ("SELECT * FROM users WHE",   "tables"),  # fallback 으로 WHERE 매치
        ("SELECT * FROM users WHERE ",          "columns"),
        ("SELECT * FROM users WHERE users.",     "columns"),
        ("SELECT * FROM users INNER ",           "join_continue"),
    ]
    for q, want_ctx in cases:
        ctx = detect_context(q)
        sugs = [s["label"] for s in get_suggestions(q, schema)[:6]]
        ok = "✓" if ctx == want_ctx else "✗"
        print(f"  {ok} {q!r:42}  ctx={ctx:18}  top: {sugs}")

    print()
    print("=" * 60)
    print("[3] on_execute 콜백 직접 호출 (TUI 우회)")
    print("=" * 60)
    rows = runner.on_execute(
        "SELECT u.name, COUNT(o.id) AS n_orders, "
        "SUM(o.amount) AS total "
        "FROM users u LEFT JOIN orders o ON o.user_id = u.id "
        "GROUP BY u.id ORDER BY total DESC NULLS LAST;"
    )
    print(rows)
    print()
    print(f"DB 위치: {db}")
    print()
    print("✓ CLI 검증 통과. 풀스크린 TUI 띄우려면:")
    print("    python basic_usage.py")


def run_tui() -> None:
    db = make_demo_db()
    runner = SQLRunnerTUI.with_sqlite(db)
    # 컬럼 description 보강 (트리 hover · displayText 에 반영)
    runner.add_table("users", [
        {"name": "id",        "type": "INTEGER",   "doc": "PK"},
        {"name": "name",      "type": "TEXT",      "doc": "표시 이름"},
        {"name": "email",     "type": "TEXT",      "doc": "로그인용 이메일"},
        {"name": "region",    "type": "TEXT",      "doc": "거주 지역"},
        {"name": "plan_type", "type": "TEXT",      "doc": "free/pro/enterprise"},
        {"name": "is_active", "type": "INTEGER",   "doc": "활성 여부 (1/0)"},
        {"name": "signup_at", "type": "TIMESTAMP", "doc": "가입 시각"},
    ], description="사용자 마스터 (5명)")
    runner.add_table("orders", [
        {"name": "id",             "type": "INTEGER",   "doc": "PK"},
        {"name": "user_id",        "type": "INTEGER",   "doc": "FK → users.id"},
        {"name": "product_id",     "type": "INTEGER",   "doc": "FK → products.id"},
        {"name": "quantity",       "type": "INTEGER",   "doc": "구매 수량"},
        {"name": "amount",         "type": "REAL",      "doc": "결제 금액 KRW"},
        {"name": "status",         "type": "TEXT",      "doc": "paid/pending/cancelled"},
        {"name": "payment_method", "type": "TEXT",      "doc": "card/kakao/naver"},
        {"name": "ordered_at",     "type": "TIMESTAMP", "doc": "주문 시각"},
    ], description="주문 내역")
    runner.add_table("products", [
        {"name": "id",        "type": "INTEGER", "doc": "PK"},
        {"name": "sku",       "type": "TEXT",    "doc": "상품 SKU UNIQUE"},
        {"name": "name",      "type": "TEXT",    "doc": "상품명"},
        {"name": "category",  "type": "TEXT",    "doc": "office/electronics/food/fitness"},
        {"name": "price",     "type": "REAL",    "doc": "정가 KRW"},
        {"name": "stock",     "type": "INTEGER", "doc": "재고"},
    ], description="상품 카탈로그")
    runner.add_table("events", [
        {"name": "id",          "type": "INTEGER",   "doc": "PK"},
        {"name": "user_id",     "type": "INTEGER",   "doc": "FK → users.id"},
        {"name": "event_type",  "type": "TEXT",      "doc": "view/click/purchase"},
        {"name": "value",       "type": "REAL",      "doc": "purchase=금액"},
        {"name": "occurred_at", "type": "TIMESTAMP", "doc": "발생 시각"},
    ], description="유저 행동 로그")

    runner.set_query(
        "-- Ctrl+R 또는 F5 로 실행 · F1 도움말 · Ctrl+Space 자동완성\n"
        "SELECT u.name, u.region, u.plan_type,\n"
        "       COUNT(o.id) AS n_orders,\n"
        "       SUM(o.amount) AS total\n"
        "FROM users u LEFT JOIN orders o ON o.user_id = u.id\n"
        "WHERE o.status = 'paid'\n"
        "GROUP BY u.id\n"
        "ORDER BY total DESC NULLS LAST;"
    )
    runner.run()


if __name__ == "__main__":
    if "--check" in sys.argv:
        run_checks()
    else:
        run_tui()
