"""Gold-accuracy scorer for a BIRD financial live_eval run.

The live harness records sqlproctor's *verdicts* (blocked / verified), not whether the
answer is *correct*. This scores correctness against BIRD's verified gold SQL, and
separates the two things that matter:

  guard-OFF accuracy = the model's FIRST query (what it would have returned unguarded)
  guard-ON  accuracy = the model's FINAL verified query (what the guard let through)

Both the model's SQL and the gold SQL are executed against the real financial.sqlite
(SQLite, so BIRD's gold keeps its exact semantics), and result sets are compared
BIRD-style: order-insensitive multiset of row tuples, with 2-decimal numeric tolerance.

Because sqlproctor is a specialized guard (multiplicative/structural errors), the overall
uplift is reported alongside the subset where sqlproctor actually fired, and any case where
it blocked a query that was in fact gold-correct (a false positive) is called out.

Usage:
  python demo/bird_gold.py results/transcripts/bird_financial_*.json
  python demo/bird_gold.py --selftest
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import sys
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parents[1]
SQLITE = ROOT / "examples" / "bird" / "financial.sqlite"
QUESTIONS_JSON = ROOT / "examples" / "bird" / "mini_dev_sqlite.json"


def load_financial() -> list[dict]:
    data = json.load(open(QUESTIONS_JSON))
    return [q for q in data if q.get("db_id") == "financial"]


def _cell(v):
    return round(v, 2) if isinstance(v, float) else v


def _norm(rows) -> Counter:
    """Order-insensitive multiset of row tuples (positional, BIRD-style)."""
    return Counter(tuple(_cell(c) for c in row) for row in rows)


def run_sql(con, sql):
    """(ok, rows). ok=False if the SQL errors (e.g. gold uses a dialect quirk)."""
    if not sql:
        return False, None
    try:
        return True, con.execute(sql).fetchall()
    except Exception:
        return False, None


def match(a_rows, b_rows) -> bool:
    return a_rows is not None and b_rows is not None and _norm(a_rows) == _norm(b_rows)


def _final_verified(trace):
    return next((t["sql"] for t in reversed(trace) if t["status"] == "verified"), None)


def score(transcript_path: str) -> dict:
    d = json.load(open(transcript_path))
    recs = d["records"]
    fin = load_financial()
    if len(recs) != len(fin):
        print(f"  WARNING: {len(recs)} records vs {len(fin)} gold questions; "
              "index alignment may be off.")
    con = sqlite3.connect(f"file:{SQLITE}?mode=ro", uri=True)

    n = off_ok = on_ok = fired = fired_fixed = false_pos = gold_unrunnable = 0
    rows_out = []
    for i, rec in enumerate(recs):
        if i >= len(fin):
            break
        gold_sql = fin[i]["SQL"]
        ok_gold, gold_rows = run_sql(con, gold_sql)
        if not ok_gold:
            gold_unrunnable += 1
            continue
        n += 1
        trace = rec.get("trace", [])
        first_sql = trace[0]["sql"] if trace else None
        final_sql = _final_verified(trace)
        _, off_rows = run_sql(con, first_sql)
        _, on_rows = run_sql(con, final_sql)
        off_m = match(off_rows, gold_rows)
        on_m = match(on_rows, gold_rows)
        off_ok += off_m
        on_ok += on_m
        blocked = rec.get("first_blocked", False)
        if blocked:
            fired += 1
            if (not off_m) and on_m:
                fired_fixed += 1
            if off_m:              # the first query was actually gold-correct but blocked
                false_pos += 1
        rows_out.append({"q": rec["question"][:60], "blocked": blocked,
                         "off": off_m, "on": on_m})
    con.close()
    return {"n": n, "off_ok": off_ok, "on_ok": on_ok, "fired": fired,
            "fired_fixed": fired_fixed, "false_pos": false_pos,
            "gold_unrunnable": gold_unrunnable, "rows": rows_out,
            "model": d.get("meta", {}).get("model", "?")}


def _report(s: dict):
    n = s["n"] or 1
    print("=" * 66)
    print(f"BIRD financial gold-accuracy  [{s['model']}]")
    print("=" * 66)
    print(f"  scored questions (gold runnable):     {s['n']}")
    if s["gold_unrunnable"]:
        print(f"  gold not runnable in sqlite (skipped): {s['gold_unrunnable']}")
    print(f"  guard-OFF execution accuracy:         {s['off_ok']}/{s['n']}  ({s['off_ok']/n:.0%})")
    print(f"  guard-ON  execution accuracy:         {s['on_ok']}/{s['n']}  ({s['on_ok']/n:.0%})")
    print(f"  net uplift from the guard:            {(s['on_ok']-s['off_ok'])/n:+.0%}")
    print(f"  sqlproctor fired (first query blocked):  {s['fired']}/{s['n']}")
    print(f"    of those, wrong -> right:           {s['fired_fixed']}")
    print(f"    of those, blocked a gold-correct q: {s['false_pos']}  (false positives)")
    print("=" * 66)


def selftest() -> int:
    """Validate the scorer's math on a synthetic transcript (no model, no API).
    Question A: first query already equals gold -> both off and on correct.
    Question B: first query is a wrong fan-out (blocked), final is correct -> off wrong,
    on right, and it counts as a fired->fixed."""
    fin = load_financial()
    gold_a, gold_b = fin[0]["SQL"], fin[1]["SQL"]
    con = sqlite3.connect(f"file:{SQLITE}?mode=ro", uri=True)
    # a wrong query for B: fan-out account against two children, guaranteed != gold_b
    wrong_b = ("SELECT SUM(t.amount) FROM account a "
               "JOIN trans t ON a.account_id=t.account_id "
               "JOIN loan l ON a.account_id=l.account_id")
    assert not match(run_sql(con, wrong_b)[1], run_sql(con, gold_b)[1])
    con.close()
    fake = {"meta": {"model": "selftest"}, "records": [
        {"question": "A", "first_blocked": False,
         "trace": [{"sql": gold_a, "status": "verified", "kinds": []}]},
        {"question": "B", "first_blocked": True,
         "trace": [{"sql": wrong_b, "status": "blocked", "kinds": ["FAN_OUT"]},
                   {"sql": gold_b, "status": "verified", "kinds": []}]},
    ]}
    tmp = ROOT / "results" / "_bird_selftest_transcript.json"
    tmp.parent.mkdir(exist_ok=True)
    json.dump(fake, open(tmp, "w"))
    s = score(str(tmp))
    tmp.unlink()
    assert s["n"] == 2, s
    assert s["off_ok"] == 1 and s["on_ok"] == 2, s        # A off+on ok; B only on ok
    assert s["fired"] == 1 and s["fired_fixed"] == 1, s   # B fired and was fixed
    assert s["false_pos"] == 0, s
    print("bird_gold selftest OK: guard-off 1/2, guard-on 2/2, 1 fired->fixed.")
    return 0


def main(argv) -> int:
    if "--selftest" in argv:
        return selftest()
    paths = [a for a in argv if not a.startswith("-")]
    if not paths:
        print("usage: python demo/bird_gold.py <transcript.json> | --selftest")
        return 2
    _report(score(paths[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
