import json

from sqlproctor import results


def test_dedupes_by_key_and_accumulates_models(tmp_path):
    results.save_result(tmp_path, "live_eval",
                        {"model": "opus", "contract_sha256": "abc", "first_blocked": 2})
    results.save_result(tmp_path, "live_eval",
                        {"model": "sonnet", "contract_sha256": "abc", "first_blocked": 1})
    # re-running the same (git_sha, model, contract) replaces its row, not appends
    results.save_result(tmp_path, "live_eval",
                        {"model": "opus", "contract_sha256": "abc", "first_blocked": 0})

    rows = [json.loads(x) for x in (tmp_path / "live_eval.jsonl").read_text().splitlines() if x.strip()]
    assert len(rows) == 2  # opus (updated) + sonnet
    opus = next(r for r in rows if r["model"] == "opus")
    assert opus["first_blocked"] == 0  # replaced, not duplicated


def test_render_writes_a_markdown_table(tmp_path):
    results.save_result(tmp_path, "benchmark",
                        {"model": "deterministic", "contract_sha256": "abc",
                         "catch_rate": 0.75, "kinds": {"FAN_OUT": 2}})
    md = (tmp_path / "RESULTS.md").read_text()
    assert "## benchmark" in md
    assert "catch_rate" in md
    assert '{"FAN_OUT":2}' in md   # nested dicts render as compact JSON


def test_records_carry_timestamp(tmp_path):
    rec = results.save_result(tmp_path, "benchmark", {"model": "x", "contract_sha256": "y"})
    assert rec["ts"].endswith("Z")
    assert rec["kind"] == "benchmark"
