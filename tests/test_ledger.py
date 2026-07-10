import json

from sqlproctor import ledger
from sqlproctor.violation import Violation

FIELDS = {"ts", "id", "client", "sql", "sql_sha256", "verdict", "violations",
          "contract_version", "contract_sha256", "dialect", "row_count", "duration_ms"}


def test_record_has_all_fields(contract):
    rec = ledger.record("SELECT 1", [], contract, client="cli", row_count=1)
    assert FIELDS <= set(rec)
    assert rec["verdict"] == "verified"
    assert rec["row_count"] == 1
    assert rec["contract_sha256"] == contract.content_hash()


def test_blocked_record_has_null_row_count(contract):
    rec = ledger.record("SELECT bad", [Violation("FAN_OUT", "inflated")], contract, client="mcp")
    assert rec["verdict"] == "blocked"
    assert rec["row_count"] is None
    assert rec["violations"][0]["kind"] == "FAN_OUT"


def test_append_writes_one_line_per_record(tmp_path, contract):
    p = tmp_path / "ledger.jsonl"
    ledger.append(ledger.record("SELECT 1", [], contract, client="cli"), str(p))
    ledger.append(ledger.record("SELECT 2", [], contract, client="cli"), str(p))
    lines = p.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["sql"] == "SELECT 1"
    assert json.loads(lines[1])["sql"] == "SELECT 2"
