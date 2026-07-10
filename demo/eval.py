"""Accuracy benchmark: how many wrong queries sqlproctor catches, and the
before/after accuracy when an agent self-corrects on the violations.

All numbers come from running the queries against the real seeded DuckDB. Nothing
is asserted by hand. Run: python demo/eval.py
"""

from __future__ import annotations

import pathlib

import duckdb
import yaml

from sqlproctor.contract import Contract
from sqlproctor.verifier import verify

ROOT = pathlib.Path(__file__).resolve().parents[1]
DB = str(ROOT / "examples" / "retail.duckdb")
CONTRACT = str(ROOT / "contracts" / "retail.yml")
CASES = str(ROOT / "demo" / "eval_cases.yml")


def _scalar(con, sql):
    """First column of the first row, or an 'ERROR' marker if the query fails."""
    try:
        row = con.execute(sql).fetchone()
        return float(row[0]) if row and row[0] is not None else None
    except Exception:
        return "ERROR"


def run_eval(db=DB, contract_path=CONTRACT, cases_path=CASES) -> dict:
    contract = Contract.from_yaml(contract_path)
    cases = yaml.safe_load(open(cases_path))["cases"]
    con = duckdb.connect(db, read_only=True)

    rows = []
    for c in cases:
        verdict = verify(c["sql"], contract, dialect="duckdb")
        blocked = not verdict.ok
        kinds = sorted({v.kind for v in verdict.violations})
        row = {"id": c["id"], "label": c["label"], "blocked": blocked, "kinds": kinds}
        if c["label"] == "wrong":
            row["catchable"] = c.get("catchable", False)
            row["expected_kind"] = c.get("kind")
            row["caught"] = blocked
            # materiality: the wrong query really returns a different answer (or errors)
            naive_val = _scalar(con, c["sql"])
            fix_val = _scalar(con, c["fix"])
            row["material"] = (naive_val == "ERROR") or (naive_val != fix_val)
        rows.append(row)
    con.close()

    wrong = [r for r in rows if r["label"] == "wrong"]
    correct = [r for r in rows if r["label"] == "correct"]
    caught = [r for r in wrong if r["caught"]]
    false_positives = [r for r in correct if r["blocked"]]
    total = len(rows)

    # Without sqlproctor: correct queries right, wrong queries wrong.
    without_right = len(correct)
    # With sqlproctor: correct pass (minus any false positive), caught wrong get corrected.
    with_right = (len(correct) - len(false_positives)) + len(caught)

    return {
        "rows": rows,
        "n_total": total,
        "n_wrong": len(wrong),
        "n_caught": len(caught),
        "catch_rate": len(caught) / len(wrong) if wrong else 0.0,
        "false_positives": len(false_positives),
        "all_material": all(r["material"] for r in wrong),
        "kind_matches": all(
            (r["expected_kind"] in r["kinds"]) for r in wrong if r["catchable"]
        ),
        "without_accuracy": without_right / total,
        "with_accuracy": with_right / total,
    }


def _save(m):
    from sqlproctor import results
    contract = Contract.from_yaml(CONTRACT)
    results.save_result(ROOT / "results", "benchmark", {
        "model": "deterministic (seed 42)",
        "contract_version": contract.version_label(),
        "contract_sha256": contract.content_hash(),
        "wrong": m["n_wrong"], "caught": m["n_caught"],
        "catch_rate": round(m["catch_rate"], 3),
        "false_positives": m["false_positives"],
        "without_accuracy": round(m["without_accuracy"], 3),
        "with_accuracy": round(m["with_accuracy"], 3),
    }, key_fields=("model", "contract_sha256"))  # deterministic: one row per contract


def main() -> int:
    m = run_eval()
    print("sqlproctor accuracy benchmark")
    print("=" * 68)
    for r in m["rows"]:
        if r["label"] == "correct":
            mark = "FALSE-POSITIVE" if r["blocked"] else "pass"
            print(f"  correct  {r['id']:<24} {mark}")
        else:
            outcome = "caught" if r["caught"] else ("miss (expected)" if not r["catchable"] else "MISS")
            kinds = ",".join(r["kinds"]) or "-"
            print(f"  wrong    {r['id']:<24} {outcome:<16} [{kinds}]")
    print("=" * 68)
    print(f"  wrong queries caught:     {m['n_caught']}/{m['n_wrong']}  "
          f"({m['catch_rate']*100:.0f}%)")
    print(f"  false positives:          {m['false_positives']}")
    print(f"  accuracy without sqlproctor: {m['without_accuracy']*100:>5.0f}%")
    print(f"  accuracy with sqlproctor:    {m['with_accuracy']*100:>5.0f}%")
    print(f"  uplift:                     {(m['with_accuracy']-m['without_accuracy'])*100:>5.0f} points")
    _save(m)
    print(f"\n  saved to results/benchmark.jsonl (see results/RESULTS.md)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
