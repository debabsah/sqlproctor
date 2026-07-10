"""A stand-in for a real database MCP server (e.g. a SQL Server or Postgres estate tool).

It exposes one `query` tool that runs SQL against the retail DuckDB and returns
rows. sqlproctor's pass-through mode points at this in tests and the local demo,
so the proxy is proven end-to-end without touching a real warehouse. Swap this
command for the real upstream and nothing else changes.
"""

from __future__ import annotations

import datetime
import os
from decimal import Decimal

import duckdb
from mcp.server.fastmcp import FastMCP

DB = os.environ.get("MOCK_UPSTREAM_DB", "examples/retail.duckdb")

mcp = FastMCP("mock-upstream")


def _cell(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()
    return v


@mcp.tool()
def query(sql: str) -> dict:
    """Run a SQL query and return the rows (no verification: this is the raw upstream)."""
    con = duckdb.connect(DB, read_only=True)
    try:
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        return {
            "columns": cols,
            "rows": [[_cell(c) for c in r] for r in rows[:1000]],
            "row_count": len(rows),
        }
    finally:
        con.close()


def main():
    mcp.run()


if __name__ == "__main__":
    main()
