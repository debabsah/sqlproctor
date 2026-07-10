"""Seed a reproducible retail DuckDB where a naive shipments join inflates revenue.

Deterministic (random.seed(42)) so every demo and eval number is byte-stable.
The point: revenue = SUM(order_items.net_amount). Each order has several shipments,
so joining shipments multiplies every order_item row by that order's shipment count,
and the summed revenue balloons by roughly the average shipments per order (~2.5x).

Run: python examples/seed.py
"""

from __future__ import annotations

import os
import pathlib
import random
from datetime import date, timedelta

import duckdb

HERE = pathlib.Path(__file__).resolve().parent
DB_PATH = os.environ.get("SQLPROCTOR_DB", str(HERE / "retail.duckdb"))

REGIONS = ["West", "East", "North", "South", "Central"]
CARRIERS = ["UPS", "FedEx", "USPS", "DHL"]
STATUSES = ["completed", "completed", "completed", "pending", "cancelled"]
SHIPMENTS_PER_ORDER = [(1, 0.20), (2, 0.30), (3, 0.30), (4, 0.20)]  # mean = 2.5
BASE_DATE = date(2025, 1, 1)

SCHEMA = """
CREATE TABLE customers (
    customer_id INTEGER PRIMARY KEY,
    region      VARCHAR,
    created_at  DATE
);
CREATE TABLE orders (
    order_id    INTEGER PRIMARY KEY,
    customer_id INTEGER REFERENCES customers(customer_id),
    ordered_at  DATE,
    status      VARCHAR,
    deleted_at  DATE
);
CREATE TABLE order_items (
    item_id    INTEGER PRIMARY KEY,
    order_id   INTEGER REFERENCES orders(order_id),
    product_id INTEGER,
    net_amount DECIMAL(10, 2),
    quantity   INTEGER
);
CREATE TABLE shipments (
    shipment_id INTEGER PRIMARY KEY,
    order_id    INTEGER REFERENCES orders(order_id),
    carrier     VARCHAR,
    shipped_at  DATE
);
"""


def _weighted(rng: random.Random, pairs) -> int:
    r, acc = rng.random(), 0.0
    for value, weight in pairs:
        acc += weight
        if r <= acc:
            return value
    return pairs[-1][0]


def build(db_path: str = DB_PATH, *, seed: int = 42,
          n_customers: int = 500, n_orders: int = 5000) -> str:
    rng = random.Random(seed)
    if os.path.exists(db_path):
        os.remove(db_path)
    con = duckdb.connect(db_path)
    con.execute(SCHEMA)

    customers = [
        (cid, rng.choice(REGIONS), BASE_DATE + timedelta(days=rng.randint(0, 300)))
        for cid in range(1, n_customers + 1)
    ]

    orders, order_items, shipments = [], [], []
    item_id = ship_id = 0
    for oid in range(1, n_orders + 1):
        cid = rng.randint(1, n_customers)
        ordered = BASE_DATE + timedelta(days=rng.randint(0, 364))
        deleted = ordered + timedelta(days=rng.randint(1, 30)) if rng.random() < 0.10 else None
        orders.append((oid, cid, ordered, rng.choice(STATUSES), deleted))

        for _ in range(rng.choice([2, 3, 4])):          # ~3 items per order
            item_id += 1
            order_items.append((item_id, oid, rng.randint(1, 200),
                                round(rng.uniform(20, 500), 2), rng.randint(1, 5)))

        for _ in range(_weighted(rng, SHIPMENTS_PER_ORDER)):  # avg 2.5 shipments/order
            ship_id += 1
            shipments.append((ship_id, oid, rng.choice(CARRIERS),
                              ordered + timedelta(days=rng.randint(1, 10))))

    con.executemany("INSERT INTO customers VALUES (?, ?, ?)", customers)
    con.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?)", orders)
    con.executemany("INSERT INTO order_items VALUES (?, ?, ?, ?, ?)", order_items)
    con.executemany("INSERT INTO shipments VALUES (?, ?, ?, ?)", shipments)
    con.close()
    return db_path


TRUE_REVENUE_SQL = """
SELECT SUM(oi.net_amount)
FROM orders o
JOIN order_items oi ON o.order_id = oi.order_id
WHERE o.deleted_at IS NULL
"""

NAIVE_REVENUE_SQL = """
SELECT SUM(oi.net_amount)
FROM orders o
JOIN order_items oi ON o.order_id = oi.order_id
JOIN shipments s ON o.order_id = s.order_id
WHERE o.deleted_at IS NULL
"""


def revenue_numbers(db_path: str = DB_PATH):
    """Returns (true_revenue, naive_revenue, ratio) from the real seeded DB."""
    con = duckdb.connect(db_path, read_only=True)
    true_rev = float(con.execute(TRUE_REVENUE_SQL).fetchone()[0])
    naive_rev = float(con.execute(NAIVE_REVENUE_SQL).fetchone()[0])
    con.close()
    return true_rev, naive_rev, naive_rev / true_rev


def _counts(db_path: str):
    con = duckdb.connect(db_path, read_only=True)
    q = lambda sql: con.execute(sql).fetchone()[0]
    out = {
        "active_orders": q("SELECT COUNT(*) FROM orders WHERE deleted_at IS NULL"),
        "order_items": q("SELECT COUNT(*) FROM order_items"),
        "shipments": q("SELECT COUNT(*) FROM shipments"),
        "orders": q("SELECT COUNT(*) FROM orders"),
    }
    con.close()
    return out


def main() -> int:
    path = build()
    c = _counts(path)
    true_rev, naive_rev, ratio = revenue_numbers(path)
    print(f"Seeded {path}")
    print(f"  orders:          {c['orders']:>10,}  ({c['active_orders']:,} active)")
    print(f"  order_items:     {c['order_items']:>10,}")
    print(f"  shipments:       {c['shipments']:>10,}  (avg {c['shipments']/c['orders']:.2f} per order)")
    print(f"  true revenue:    {true_rev:>14,.2f}   SUM(net_amount) over active orders")
    print(f"  naive revenue:   {naive_rev:>14,.2f}   with a shipments fan-out join")
    print(f"  inflation:       {ratio:>10.2f}x")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
