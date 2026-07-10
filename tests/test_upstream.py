"""Pass-through mode over real stdio: sqlproctor verifies, then forwards clean
queries to an upstream MCP server (here, examples/mock_upstream.py standing in for
a real database MCP server). A blocked query must never reach the upstream.
"""

import asyncio
import json
import pathlib
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = pathlib.Path(__file__).resolve().parents[1]
QUERIES = ROOT / "examples" / "queries"


def _result(res) -> dict:
    if res.structuredContent:
        return res.structuredContent
    return json.loads(res.content[0].text)


async def _drive(ledger_path):
    env = {
        "PATH": __import__("os").environ.get("PATH", ""),
        "SQLPROCTOR_CONTRACT": str(ROOT / "contracts" / "retail.yml"),
        "SQLPROCTOR_LEDGER": str(ledger_path),
        "SQLPROCTOR_DIALECT": "duckdb",
        "SQLPROCTOR_UPSTREAM_CMD": f"{sys.executable} {ROOT / 'examples' / 'mock_upstream.py'}",
        "MOCK_UPSTREAM_DB": str(ROOT / "examples" / "retail.duckdb"),
    }
    params = StdioServerParameters(command=sys.executable,
                                   args=["-m", "sqlproctor.mcp_server"], env=env)
    import os
    with open(os.devnull, "w") as devnull:
        async with stdio_client(params, errlog=devnull) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                bad = _result(await session.call_tool(
                    "query", {"sql": (QUERIES / "revenue_by_region_bad.sql").read_text()}))
                good = _result(await session.call_tool(
                    "query", {"sql": (QUERIES / "revenue_by_region_good.sql").read_text()}))
                return bad, good


def test_passthrough_blocks_and_forwards(seeded_db, tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    bad, good = asyncio.run(_drive(ledger))

    # blocked query is stopped before the upstream is ever contacted
    assert bad["status"] == "blocked"
    assert any(v["kind"] == "FAN_OUT" for v in bad["violations"])
    assert "upstream" not in bad

    # clean query is forwarded and the upstream rows come back, tagged verified
    assert good["status"] == "verified"
    assert good["verified_against"] == "v1"
    assert good["upstream"]["row_count"] > 0
    assert "region" in good["upstream"]["columns"]

    verdicts = [json.loads(x)["verdict"] for x in ledger.read_text().strip().splitlines()]
    assert verdicts == ["blocked", "verified"]
    clients = {json.loads(x)["client"] for x in ledger.read_text().strip().splitlines()}
    assert clients == {"mcp-proxy"}
