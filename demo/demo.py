"""The end-to-end loop, driving the real sqlproctor MCP server over stdio.

An agent asks one ordinary question, writes the naive query, gets blocked with an
explanation, corrects itself, and gets the verified answer. Block and verify
decisions and every number come from the real server and the real seeded DuckDB.
The only scripted part is the agent's choice to retry with the corrected query.

Run: python demo/demo.py
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys

import duckdb
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = pathlib.Path(__file__).resolve().parents[1]
DB = ROOT / "examples" / "retail.duckdb"
CONTRACT = ROOT / "contracts" / "retail.yml"
LEDGER = ROOT / "ledger.jsonl"
QUERIES = ROOT / "examples" / "queries"

NAIVE = (QUERIES / "revenue_by_region_bad.sql").read_text()
CORRECT = (QUERIES / "revenue_by_region_good.sql").read_text()

TRUE_SQL = ("SELECT SUM(oi.net_amount) FROM orders o "
            "JOIN order_items oi ON o.order_id = oi.order_id WHERE o.deleted_at IS NULL")
NAIVE_SQL = ("SELECT SUM(oi.net_amount) FROM orders o "
             "JOIN order_items oi ON o.order_id = oi.order_id "
             "JOIN shipments s ON o.order_id = s.order_id WHERE o.deleted_at IS NULL")

RULE = "-" * 72


def _ensure_seeded():
    if not DB.exists():
        import importlib.util
        spec = importlib.util.spec_from_file_location("seed", ROOT / "examples" / "seed.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.build()


def _result(res) -> dict:
    if res.structuredContent:
        return res.structuredContent
    return json.loads(res.content[0].text)


async def _call(session, sql) -> dict:
    return _result(await session.call_tool("query", {"sql": sql}))


def _headline_numbers():
    con = duckdb.connect(str(DB), read_only=True)
    try:
        true_rev = float(con.execute(TRUE_SQL).fetchone()[0])
        naive_rev = float(con.execute(NAIVE_SQL).fetchone()[0])
        return true_rev, naive_rev
    finally:
        con.close()


async def run():
    _ensure_seeded()
    if LEDGER.exists():
        LEDGER.unlink()
    true_rev, naive_rev = _headline_numbers()

    env = {**os.environ, "SQLPROCTOR_DB": str(DB),
           "SQLPROCTOR_CONTRACT": str(CONTRACT), "SQLPROCTOR_LEDGER": str(LEDGER)}
    params = StdioServerParameters(command=sys.executable,
                                   args=["-m", "sqlproctor.mcp_server"], env=env)

    with open(os.devnull, "w") as devnull:
        async with stdio_client(params, errlog=devnull) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                print(RULE)
                print('Agent question: "What was our revenue by region?"')
                print(RULE)

                print("\n[1] The agent's first query joins shipments to reach orders:\n")
                print("    " + NAIVE.strip().replace("\n", "\n    "))
                res1 = await _call(session, NAIVE)
                print(f"\n    -> {res1['status'].upper()} by contract {res1['contract_version']}")
                for v in res1["violations"]:
                    print(f"       [{v['kind']}] {v['message']}")
                    if v["suggested_fix"]:
                        print(f"       fix: {v['suggested_fix']}")
                print(f"\n    Had this run, the agent would have reported ${naive_rev:,.0f} in revenue")
                print(f"    ({naive_rev/true_rev:.2f}x the truth). That number reaches a dashboard and")
                print("    nobody notices it is wrong.")

                print("\n[2] The agent reads the violation, drops the shipments join, and retries:\n")
                print("    " + CORRECT.strip().replace("\n", "\n    "))
                res2 = await _call(session, CORRECT)
                print(f"\n    -> {res2['status'].upper()}, verified against contract {res2['verified_against']}")
                total = sum(r[1] for r in res2["rows"])
                for region, rev in res2["rows"]:
                    print(f"       {region:<10} ${rev:>14,.2f}")
                print(f"       {'TOTAL':<10} ${total:>14,.2f}   (the true number)")

    print("\n" + RULE)
    print("Ledger (every attempt, blocked and verified):")
    for line in LEDGER.read_text().strip().splitlines():
        rec = json.loads(line)
        detail = (",".join(v["kind"] for v in rec["violations"])
                  or f"{rec['row_count']} rows")
        print(f"  {rec['verdict']:<9} {detail:<22} contract {rec['contract_version']}")
    print(RULE)


def main() -> int:
    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
