import json
import pathlib

import pytest

from sqlproctor import mcp_server

ROOT = pathlib.Path(__file__).resolve().parents[1]
QUERIES = ROOT / "examples" / "queries"


@pytest.fixture
def wired(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_server, "CONTRACT_PATH", str(ROOT / "contracts" / "retail.yml"))
    monkeypatch.setattr(mcp_server, "DB_PATH", str(ROOT / "examples" / "retail.duckdb"))
    monkeypatch.setattr(mcp_server, "LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    monkeypatch.setattr(mcp_server, "_contract", None)
    return tmp_path


def _sql(name):
    return (QUERIES / name).read_text()


def test_fan_out_query_is_blocked(wired):
    res = mcp_server.run_query(_sql("revenue_by_region_bad.sql"))
    assert res["status"] == "blocked"
    assert any(v["kind"] == "FAN_OUT" for v in res["violations"])
    assert "call query again" in res["hint"]


def test_corrected_query_is_verified(wired):
    res = mcp_server.run_query(_sql("revenue_by_region_good.sql"))
    assert res["status"] == "verified"
    assert res["verified_against"] == "v1"
    assert res["row_count"] > 0
    assert "region" in res["columns"]
    # rows are JSON-serializable (Decimal/date already coerced)
    json.dumps(res["rows"])


def test_both_outcomes_land_in_ledger_in_order(wired):
    mcp_server.run_query(_sql("revenue_by_region_bad.sql"))
    mcp_server.run_query(_sql("revenue_by_region_good.sql"))
    lines = (wired / "ledger.jsonl").read_text().strip().splitlines()
    assert [json.loads(x)["verdict"] for x in lines] == ["blocked", "verified"]


def test_write_query_is_blocked_read_only(wired):
    res = mcp_server.run_query("DELETE FROM orders")
    assert res["status"] == "blocked"
    assert any(v["kind"] == "READ_ONLY" for v in res["violations"])


def test_verified_but_unrunnable_query_returns_error_not_crash(wired, monkeypatch):
    # A query can pass the contract yet fail in the database (a dialect quirk the
    # permissive verifier tolerated, an upstream outage). That must be a structured
    # outcome the agent can self-correct on, never an exception that kills the caller.
    def boom(sql):
        raise RuntimeError("db exploded")
    monkeypatch.setattr(mcp_server, "execute", boom)
    res = mcp_server.run_query(_sql("revenue_by_region_good.sql"))
    assert res["status"] == "error"
    assert "db exploded" in res["error"]
    assert "call query again" in res["hint"]
