"""Append-only provenance ledger: one JSON line per verified query attempt.

Blocked attempts are logged too, so the ledger reconstructs the whole
block -> correct -> verified exchange. This is the audit trail that MCP
deployments are widely noted to lack.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone

DEFAULT_PATH = os.environ.get("SQLPROCTOR_LEDGER", "ledger.jsonl")


def record(sql, violations, contract, *, client, row_count=None,
           duration_ms=None, dialect="duckdb") -> dict:
    return {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "id": str(uuid.uuid4()),
        "client": client,
        "sql": sql,
        "sql_sha256": hashlib.sha256(sql.encode()).hexdigest(),
        "verdict": "verified" if not violations else "blocked",
        "violations": [v.to_dict() for v in violations],
        "contract_version": contract.version_label(),
        "contract_sha256": contract.content_hash(),
        "dialect": dialect,
        "row_count": row_count,
        "duration_ms": duration_ms,
    }


def append(rec: dict, path: str | None = None) -> dict:
    path = path or DEFAULT_PATH
    with open(path, "a") as f:
        f.write(json.dumps(rec) + "\n")
        f.flush()
    return rec
