"""The BIRD `financial` database as a sqlproctor schema.

Unlike the other seeds, the data is NOT generated here: it is the real financial.sqlite
shipped with the BIRD mini-dev benchmark (external, CC BY-SA 4.0). Fetch it once and
place it at examples/bird/financial.sqlite (see docs); sqlproctor's execute() seam runs
SQLite directly when SQLPROCTOR_DB is a .sqlite file, so BIRD's own gold SQL keeps its
exact semantics (strftime arg order, integer division) instead of being re-interpreted
by DuckDB.

The point of this schema: measure guard-off vs guard-on execution accuracy against
BIRD's verified gold, on real dirty data, with the fan-out hub `account` (parent of
disp, loan, order, trans).
"""

from __future__ import annotations

import os
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
DB_PATH = os.environ.get("SQLPROCTOR_DB", str(HERE / "bird" / "financial.sqlite"))
QUESTIONS_JSON = HERE / "bird" / "mini_dev_sqlite.json"

# Raw DDL shown to the model (no contract rules). Mirrors the real financial.sqlite.
SCHEMA = """
CREATE TABLE district (
    district_id INTEGER PRIMARY KEY, A2 TEXT, A3 TEXT, A4 TEXT, A5 TEXT, A6 TEXT,
    A7 TEXT, A8 INTEGER, A9 INTEGER, A10 REAL, A11 INTEGER, A12 REAL, A13 REAL,
    A14 INTEGER, A15 INTEGER, A16 INTEGER
);
CREATE TABLE account (
    account_id INTEGER PRIMARY KEY, district_id INTEGER, frequency TEXT, date DATE
);
CREATE TABLE client (
    client_id INTEGER PRIMARY KEY, gender TEXT, birth_date DATE, district_id INTEGER
);
CREATE TABLE disp (
    disp_id INTEGER PRIMARY KEY, client_id INTEGER, account_id INTEGER, type TEXT
);
CREATE TABLE card (
    card_id INTEGER PRIMARY KEY, disp_id INTEGER, type TEXT, issued DATE
);
CREATE TABLE loan (
    loan_id INTEGER PRIMARY KEY, account_id INTEGER, date DATE, amount INTEGER,
    duration INTEGER, payments REAL, status TEXT
);
CREATE TABLE "order" (
    order_id INTEGER PRIMARY KEY, account_id INTEGER, bank_to TEXT, account_to INTEGER,
    amount REAL, k_symbol TEXT
);
CREATE TABLE trans (
    trans_id INTEGER PRIMARY KEY, account_id INTEGER, date DATE, type TEXT,
    operation TEXT, amount INTEGER, balance INTEGER, k_symbol TEXT, bank TEXT,
    account INTEGER
);
"""


def build(db_path: str = DB_PATH) -> str:
    """The data is external. Verify it is present; do not fabricate it."""
    if not os.path.exists(db_path):
        raise FileNotFoundError(
            f"BIRD financial.sqlite not found at {db_path}. Fetch the BIRD mini-dev set "
            "(https://bird-bench.github.io/) and place financial.sqlite at "
            "examples/bird/financial.sqlite.")
    return db_path


def load_financial(json_path: pathlib.Path = QUESTIONS_JSON) -> list[dict]:
    """The 32 BIRD `financial` items, in file order: question, evidence, gold SQL."""
    import json
    if not json_path.exists():
        return []
    data = json.load(open(json_path))
    return [q for q in data if q.get("db_id") == "financial"]


def questions() -> list[str]:
    """One prompt string per item: the question plus BIRD's evidence (the domain
    knowledge BIRD intends the model to have), so failures are SQL-correctness
    failures, not knowledge gaps."""
    out = []
    for q in load_financial():
        ev = (q.get("evidence") or "").strip()
        out.append(f"{q['question']} (Context: {ev})" if ev else q["question"])
    return out


# A fan-out that must be blocked, and a correct query that must verify (for --selftest).
NAIVE_REVENUE_SQL = (
    "SELECT SUM(t.amount) FROM account a "
    "JOIN trans t ON a.account_id = t.account_id "
    "JOIN loan l ON a.account_id = l.account_id"
)
TRUE_REVENUE_SQL = "SELECT SUM(l.amount) FROM loan l"


def main() -> int:
    path = build()
    fin = load_financial()
    print(f"BIRD financial ready at {path}")
    print(f"  financial questions: {len(fin)}")
    if fin:
        print(f"  sample: {fin[0]['question']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
