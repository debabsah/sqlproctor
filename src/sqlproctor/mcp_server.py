"""sqlproctor as an MCP server: one verified `query` tool over embedded DuckDB.

Point any MCP client (e.g. Claude) at `sqlproctor serve`. Every query is verified
against the contract before it runs. Blocked queries return structured violations
the agent self-corrects on; passing queries return rows tagged with the contract
version. Both outcomes are written to the ledger.

The DuckDB call is isolated behind execute(). Swapping that one function for a
call to an upstream database MCP server turns this into a verifying pass-through
proxy (the planned Option B) with no other change.
"""

from __future__ import annotations

import datetime
import os
import time
from decimal import Decimal

import duckdb
from mcp.server.fastmcp import FastMCP

from . import ledger
from .contract import Contract
from .verifier import verify

CONTRACT_PATH = os.environ.get("SQLPROCTOR_CONTRACT", "contracts/retail.yml")
DB_PATH = os.environ.get("SQLPROCTOR_DB", "examples/retail.duckdb")
LEDGER_PATH = os.environ.get("SQLPROCTOR_LEDGER", "ledger.jsonl")
DIALECT = os.environ.get("SQLPROCTOR_DIALECT", "duckdb")  # e.g. "tsql" for SQL Server
MAX_ROWS = 1000

_contract: Contract | None = None


def get_contract() -> Contract:
    global _contract
    if _contract is None:
        _contract = Contract.from_yaml(CONTRACT_PATH)
    return _contract


def _cell(v):
    """Make a result cell JSON-serializable."""
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()
    return v


def execute(sql: str):
    """The single DB seam: DuckDB, or SQLite when SQLPROCTOR_DB is a .sqlite file (so a
    benchmark shipped as SQLite, e.g. BIRD, runs on its own engine and preserves its
    gold semantics). Option B swaps this for an upstream MCP call."""
    if DB_PATH.endswith((".sqlite", ".sqlite3", ".db")):
        import sqlite3
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        try:
            cur = con.execute(sql)
            cols = [d[0] for d in cur.description] if cur.description else []
            return cols, cur.fetchall()
        finally:
            con.close()
    con = duckdb.connect(DB_PATH, read_only=True)
    try:
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        return cols, rows
    finally:
        con.close()


def run_query(sql: str) -> dict:
    """Verify, then (if clean) execute. Pure function so it is testable directly."""
    contract = get_contract()
    start = time.perf_counter()
    verdict = verify(sql, contract, dialect=DIALECT)
    if not verdict.ok:
        ms = round((time.perf_counter() - start) * 1000, 2)
        ledger.append(ledger.record(sql, verdict.violations, contract,
                                     client="mcp", duration_ms=ms), LEDGER_PATH)
        return {
            "status": "blocked",
            "contract_version": contract.version_label(),
            "violations": [v.to_dict() for v in verdict.violations],
            "hint": "Fix the violations above and call query again.",
        }
    try:
        cols, rows = execute(sql)
    except Exception as e:  # noqa: BLE001 - the contract passed; the DB could not run it
        ms = round((time.perf_counter() - start) * 1000, 2)
        ledger.append(ledger.record(sql, [], contract, client="mcp", duration_ms=ms),
                      LEDGER_PATH)
        return {
            "status": "error",
            "verified_against": contract.version_label(),
            "error": f"{type(e).__name__}: {str(e).splitlines()[0][:200]}",
            "hint": "The query satisfied the contract but the database could not run it. "
                    "Fix the SQL error and call query again.",
        }
    ms = round((time.perf_counter() - start) * 1000, 2)
    ledger.append(ledger.record(sql, [], contract, client="mcp",
                                 row_count=len(rows), duration_ms=ms), LEDGER_PATH)
    return {
        "status": "verified",
        "verified_against": contract.version_label(),
        "columns": cols,
        "rows": [[_cell(c) for c in row] for row in rows[:MAX_ROWS]],
        "row_count": len(rows),
        "truncated": len(rows) > MAX_ROWS,
    }


async def run_query_upstream(sql: str) -> dict:
    """Verify, then forward a clean query to the upstream MCP server (pass-through)."""
    from . import upstream
    contract = get_contract()
    start = time.perf_counter()
    verdict = verify(sql, contract, dialect=DIALECT)
    if not verdict.ok:
        ms = round((time.perf_counter() - start) * 1000, 2)
        ledger.append(ledger.record(sql, verdict.violations, contract,
                                     client="mcp-proxy", dialect=DIALECT, duration_ms=ms), LEDGER_PATH)
        return {
            "status": "blocked",
            "contract_version": contract.version_label(),
            "violations": [v.to_dict() for v in verdict.violations],
            "hint": "Fix the violations above and call query again.",
        }
    payload = await upstream.forward(sql)  # never reached for a blocked query
    ms = round((time.perf_counter() - start) * 1000, 2)
    row_count = payload.get("row_count") if isinstance(payload, dict) else None
    ledger.append(ledger.record(sql, [], contract, client="mcp-proxy", dialect=DIALECT,
                                 row_count=row_count, duration_ms=ms), LEDGER_PATH)
    return {
        "status": "verified",
        "verified_against": contract.version_label(),
        "upstream": payload,
    }


mcp = FastMCP("sqlproctor")


@mcp.tool()
async def query(sql: str) -> dict:
    """Run a read-only SQL query against the warehouse.

    Every query is verified against the semantic contract before it runs. If it
    violates the contract, the response has status "blocked" with the specific
    violations and how to fix each one: correct the SQL and call query again. If
    it passes, the response has status "verified" with the rows and the contract
    version they were verified against.
    """
    from . import upstream
    if upstream.upstream_configured():
        return await run_query_upstream(sql)
    return run_query(sql)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
