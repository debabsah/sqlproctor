"""Pass-through to an upstream database MCP server.

When SQLPROCTOR_UPSTREAM_CMD is set, a query that passes verification is forwarded
to an existing database MCP server (e.g. a SQL Server estate tool) instead of the
embedded DuckDB. sqlproctor becomes a verifying proxy: same agent, same upstream,
now with a correctness gate and a ledger in front.

Config (env):
  SQLPROCTOR_UPSTREAM_CMD      command that starts the upstream stdio MCP server,
                              e.g. "python -m sqlserver_mcp"
  SQLPROCTOR_UPSTREAM_TOOL     the upstream tool that runs SQL   (default: query)
  SQLPROCTOR_UPSTREAM_SQL_ARG  that tool's SQL argument name      (default: sql)
"""

from __future__ import annotations

import json
import os
import shlex

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def upstream_configured() -> bool:
    return bool(os.environ.get("SQLPROCTOR_UPSTREAM_CMD"))


def _extract(res) -> dict:
    """Normalize an upstream CallToolResult into a plain dict, shape-agnostic."""
    if res.structuredContent:
        return res.structuredContent
    text = res.content[0].text if res.content else ""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {"raw": text}


async def forward(sql: str) -> dict:
    """Start the upstream server, call its query tool with `sql`, return the payload.

    ponytail: connects per query (a fresh upstream process each call). Correct and
    simple for dogfooding; hold a persistent session/pool if latency ever matters.
    """
    cmd = shlex.split(os.environ["SQLPROCTOR_UPSTREAM_CMD"])
    tool = os.environ.get("SQLPROCTOR_UPSTREAM_TOOL", "query")
    sql_arg = os.environ.get("SQLPROCTOR_UPSTREAM_SQL_ARG", "sql")
    params = StdioServerParameters(command=cmd[0], args=cmd[1:], env={**os.environ})
    with open(os.devnull, "w") as devnull:
        async with stdio_client(params, errlog=devnull) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                res = await session.call_tool(tool, {sql_arg: sql})
                return _extract(res)
