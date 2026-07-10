"""Seed a reproducible B2B-SaaS billing DuckDB with the same failure modes as the
retail demo, in a messier shape.

Deterministic (random.seed(42)) so every eval number is byte-stable. The traps:
  * billed revenue = SUM(invoice_line_items.amount) over non-voided invoices. Each
    invoice has several *partial payments*, so joining payments multiplies every
    line-item row by that invoice's payment count and the summed revenue balloons
    (~2x). This is the fan-out.
  * voided invoices (voided_at IS NOT NULL) and canceled subscriptions
    (canceled_at IS NOT NULL) are not real billings / not active MRR: the
    required-filter and metric-bypass traps.
  * usage_events link to accounts but NOT to subscriptions; joining them on
    account_id is an undeclared relationship (the join-path trap), and an account
    with several subscriptions makes per-subscription usage under-determined.

Run: python examples/saas_seed.py
"""

from __future__ import annotations

import os
import pathlib
import random
from datetime import date, timedelta

import duckdb

HERE = pathlib.Path(__file__).resolve().parent
DB_PATH = os.environ.get("SQLPROCTOR_DB", str(HERE / "saas.duckdb"))

REGIONS = ["AMER", "EMEA", "APAC"]
PLAN_TIERS = ["free", "pro", "pro", "business", "enterprise"]
METHODS = ["card", "card", "card", "ach", "wire"]
PAYMENTS_PER_INVOICE = [(1, 0.45), (2, 0.35), (3, 0.20)]  # mean = 1.75
BASE_DATE = date(2024, 1, 1)

SCHEMA = """
CREATE TABLE accounts (
    account_id   INTEGER PRIMARY KEY,
    plan_tier    VARCHAR,
    region       VARCHAR,
    signed_up_at DATE,
    churned_at   DATE
);
CREATE TABLE subscriptions (
    subscription_id INTEGER PRIMARY KEY,
    account_id      INTEGER REFERENCES accounts(account_id),
    started_at      DATE,
    canceled_at     DATE,
    mrr_amount      DECIMAL(10, 2)
);
CREATE TABLE invoices (
    invoice_id INTEGER PRIMARY KEY,
    account_id INTEGER REFERENCES accounts(account_id),
    issued_at  DATE,
    amount     DECIMAL(10, 2),
    voided_at  DATE
);
CREATE TABLE invoice_line_items (
    line_id    INTEGER PRIMARY KEY,
    invoice_id INTEGER REFERENCES invoices(invoice_id),
    product    VARCHAR,
    amount     DECIMAL(10, 2),
    quantity   INTEGER
);
CREATE TABLE payments (
    payment_id INTEGER PRIMARY KEY,
    invoice_id INTEGER REFERENCES invoices(invoice_id),
    paid_at    DATE,
    amount     DECIMAL(10, 2),
    method     VARCHAR
);
CREATE TABLE usage_events (
    event_id    INTEGER PRIMARY KEY,
    account_id  INTEGER REFERENCES accounts(account_id),
    occurred_at DATE,
    units       INTEGER
);
"""


def _weighted(rng: random.Random, pairs) -> int:
    r, acc = rng.random(), 0.0
    for value, weight in pairs:
        acc += weight
        if r <= acc:
            return value
    return pairs[-1][0]


def build(db_path: str = DB_PATH, *, seed: int = 42, n_accounts: int = 400) -> str:
    rng = random.Random(seed)
    if os.path.exists(db_path):
        os.remove(db_path)
    con = duckdb.connect(db_path)
    con.execute(SCHEMA)

    accounts, subscriptions, invoices = [], [], []
    line_items, payments, usage = [], [], []
    sub_id = inv_id = line_id = pay_id = evt_id = 0

    for aid in range(1, n_accounts + 1):
        signed = BASE_DATE + timedelta(days=rng.randint(0, 400))
        churned = signed + timedelta(days=rng.randint(30, 300)) if rng.random() < 0.12 else None
        accounts.append((aid, rng.choice(PLAN_TIERS), rng.choice(REGIONS), signed, churned))

        for _ in range(rng.choice([1, 1, 2, 3])):           # ~1.75 subs per account
            sub_id += 1
            canceled = signed + timedelta(days=rng.randint(30, 250)) if rng.random() < 0.18 else None
            subscriptions.append((sub_id, aid, signed, canceled, round(rng.uniform(50, 2000), 2)))

        for _ in range(rng.randint(3, 12)):                 # monthly-ish invoices
            inv_id += 1
            issued = signed + timedelta(days=rng.randint(1, 360))
            voided = issued + timedelta(days=rng.randint(1, 20)) if rng.random() < 0.08 else None
            inv_total = 0.0
            for _ in range(rng.choice([1, 2, 2, 3, 4])):    # line items per invoice
                line_id += 1
                amt = round(rng.uniform(20, 800), 2)
                inv_total += amt
                line_items.append((line_id, inv_id, f"SKU-{rng.randint(1, 40)}", amt, rng.randint(1, 10)))
            invoices.append((inv_id, aid, issued, round(inv_total, 2), voided))

            remaining = inv_total
            n_pay = _weighted(rng, PAYMENTS_PER_INVOICE)    # partial payments -> fan-out
            for k in range(n_pay):
                pay_id += 1
                share = remaining if k == n_pay - 1 else round(remaining * rng.uniform(0.3, 0.7), 2)
                remaining = round(remaining - share, 2)
                payments.append((pay_id, inv_id, issued + timedelta(days=rng.randint(1, 30)),
                                 round(share, 2), rng.choice(METHODS)))

        for _ in range(rng.randint(10, 40)):                # usage events at account grain
            evt_id += 1
            usage.append((evt_id, aid, signed + timedelta(days=rng.randint(1, 360)), rng.randint(1, 500)))

    con.executemany("INSERT INTO accounts VALUES (?, ?, ?, ?, ?)", accounts)
    con.executemany("INSERT INTO subscriptions VALUES (?, ?, ?, ?, ?)", subscriptions)
    con.executemany("INSERT INTO invoices VALUES (?, ?, ?, ?, ?)", invoices)
    con.executemany("INSERT INTO invoice_line_items VALUES (?, ?, ?, ?, ?)", line_items)
    con.executemany("INSERT INTO payments VALUES (?, ?, ?, ?, ?)", payments)
    con.executemany("INSERT INTO usage_events VALUES (?, ?, ?, ?)", usage)
    con.close()
    return db_path


TRUE_REVENUE_SQL = """
SELECT SUM(li.amount)
FROM invoices i
JOIN invoice_line_items li ON i.invoice_id = li.invoice_id
WHERE i.voided_at IS NULL
"""

NAIVE_REVENUE_SQL = """
SELECT SUM(li.amount)
FROM invoices i
JOIN invoice_line_items li ON i.invoice_id = li.invoice_id
JOIN payments p ON i.invoice_id = p.invoice_id
WHERE i.voided_at IS NULL
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
        "accounts": q("SELECT COUNT(*) FROM accounts"),
        "subscriptions": q("SELECT COUNT(*) FROM subscriptions"),
        "invoices": q("SELECT COUNT(*) FROM invoices"),
        "live_invoices": q("SELECT COUNT(*) FROM invoices WHERE voided_at IS NULL"),
        "line_items": q("SELECT COUNT(*) FROM invoice_line_items"),
        "payments": q("SELECT COUNT(*) FROM payments"),
        "usage_events": q("SELECT COUNT(*) FROM usage_events"),
    }
    con.close()
    return out


def main() -> int:
    path = build()
    c = _counts(path)
    true_rev, naive_rev, ratio = revenue_numbers(path)
    print(f"Seeded {path}")
    print(f"  accounts:        {c['accounts']:>10,}")
    print(f"  subscriptions:   {c['subscriptions']:>10,}")
    print(f"  invoices:        {c['invoices']:>10,}  ({c['live_invoices']:,} not voided)")
    print(f"  line_items:      {c['line_items']:>10,}")
    print(f"  payments:        {c['payments']:>10,}  (avg {c['payments']/c['invoices']:.2f} per invoice)")
    print(f"  usage_events:    {c['usage_events']:>10,}")
    print(f"  true revenue:    {true_rev:>14,.2f}   SUM(line_items.amount) over live invoices")
    print(f"  naive revenue:   {naive_rev:>14,.2f}   with a payments fan-out join")
    print(f"  inflation:       {ratio:>10.2f}x")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
