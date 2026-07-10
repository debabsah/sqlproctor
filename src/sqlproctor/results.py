"""Durable, provenance-stamped eval results.

Eval numbers are only presentable and citable if they persist with enough
context to reproduce them: which code produced them (git sha), against which
contract (version + content hash), with which model. Each result is one row per
(commit, model, contract); re-running at the same commit replaces the row rather
than appending noise, so different models accumulate into a comparison table.

Committed to `results/` so the numbers travel with the repo.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
from datetime import datetime, timezone

DEDUPE_KEY = ("git_sha", "model", "contract_sha256")
_FRONT_COLUMNS = ("ts", "git_sha", "model", "contract_version")


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git_sha(cwd) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(cwd), text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


def _read(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def save_result(results_dir, kind: str, payload: dict, key_fields=DEDUPE_KEY) -> dict:
    d = pathlib.Path(results_dir)
    d.mkdir(parents=True, exist_ok=True)
    rec = {"ts": _utc_now(), "git_sha": _git_sha(d), "kind": kind, **payload}
    path = d / f"{kind}.jsonl"

    def key(r):
        return tuple(r.get(f) for f in key_fields)

    rows = [r for r in _read(path) if key(r) != key(rec)]
    rows.append(rec)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    render(d)
    return rec


def render(results_dir) -> None:
    d = pathlib.Path(results_dir)
    out = [
        "# sqlproctor eval results",
        "",
        "Provenance-stamped, reproducible. Each row is one (commit, model, contract) result.",
        "",
        "`verified` / `first_blocked` describe sqlproctor's *verdicts*, not ground-truth",
        "correctness: a query sqlproctor verifies can still be wrong if the contract does not",
        "yet model that error. Read them as \"sqlproctor-approved,\" not \"correct.\"",
        "",
    ]
    for path in sorted(d.glob("*.jsonl")):
        rows = _read(path)
        if not rows:
            continue
        cols: list[str] = []
        for r in rows:
            for c in r:
                if c not in cols and c != "kind":
                    cols.append(c)
        front = [c for c in _FRONT_COLUMNS if c in cols]
        cols = front + [c for c in cols if c not in front]
        out.append(f"## {path.stem}")
        out.append("")
        out.append("| " + " | ".join(cols) + " |")
        out.append("| " + " | ".join("---" for _ in cols) + " |")
        for r in rows:
            cells = []
            for c in cols:
                v = r.get(c, "")
                if isinstance(v, (dict, list)):
                    v = json.dumps(v, separators=(",", ":"))
                cells.append(str(v))
            out.append("| " + " | ".join(cells) + " |")
        out.append("")
    (d / "RESULTS.md").write_text("\n".join(out))
