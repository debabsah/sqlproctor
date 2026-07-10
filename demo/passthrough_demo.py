"""Pass-through demo: sqlproctor as a verifying proxy in front of a separate
database MCP server.

Two independent MCP servers run here: the upstream (examples/mock_upstream.py,
standing in for a real database tool like a SQL Server estate server) and
sqlproctor in front of it. The agent talks only to sqlproctor. sqlproctor verifies
every query, and only forwards the clean ones to the upstream. Point
SQLPROCTOR_UPSTREAM_CMD at a real server and nothing else changes.

Run: python demo/passthrough_demo.py
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = pathlib.Path(__file__).resolve().parents[1]
DB = ROOT / "examples" / "retail.duckdb"
CONTRACT = ROOT / "contracts" / "retail.yml"
LEDGER = ROOT / "ledger.jsonl"
QUERIES = ROOT / "examples" / "queries"
UPSTREAM = ROOT / "examples" / "mock_upstream.py"

NAIVE = (QUERIES / "revenue_by_region_bad.sql").read_text()
CORRECT = (QUERIES / "revenue_by_region_good.sql").read_text()
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


async def run():
    _ensure_seeded()
    if LEDGER.exists():
        LEDGER.unlink()

    env = {
        "PATH": os.environ.get("PATH", ""),
        "SQLPROCTOR_CONTRACT": str(CONTRACT),
        "SQLPROCTOR_LEDGER": str(LEDGER),
        "SQLPROCTOR_DIALECT": "duckdb",
        "SQLPROCTOR_UPSTREAM_CMD": f"{sys.executable} {UPSTREAM}",
        "MOCK_UPSTREAM_DB": str(DB),
    }
    params = StdioServerParameters(command=sys.executable,
                                   args=["-m", "sqlproctor.mcp_server"], env=env)

    print(RULE)
    print("sqlproctor is proxying an upstream database MCP server ('mock-upstream').")
    print("The agent talks to sqlproctor; only verified queries reach the upstream.")
    print(RULE)

    with open(os.devnull, "w") as devnull:
        async with stdio_client(params, errlog=devnull) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                print("\n[1] Agent sends the naive revenue-by-region query (joins shipments):")
                bad = await _call(session, NAIVE)
                print(f"    -> {bad['status'].upper()} by contract {bad['contract_version']}")
                for v in bad["violations"]:
                    print(f"       [{v['kind']}] {v['message']}")
                print("    The upstream database was never queried. No wrong number left the box.")

                print("\n[2] Agent drops the shipments join and retries:")
                good = await _call(session, CORRECT)
                up = good["upstream"]
                print(f"    -> {good['status'].upper()}, verified against contract {good['verified_against']}")
                print(f"       forwarded to upstream, {up['row_count']} rows returned")
                total = sum(r[1] for r in up["rows"])
                print(f"       total revenue ${total:,.2f}  (the true number, straight from the upstream)")

    print("\n" + RULE)
    print("Ledger (client = mcp-proxy):")
    for line in LEDGER.read_text().strip().splitlines():
        rec = json.loads(line)
        detail = ",".join(v["kind"] for v in rec["violations"]) or f"{rec['row_count']} rows"
        print(f"  {rec['verdict']:<9} {detail:<22} via {rec['client']}")
    print(RULE)


def main() -> int:
    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
