"""The semantic contract: declared truth a query is checked against.

A contract is authored in YAML, versioned in git, and bootstrapped from a live
database or dbt metadata (see bootstrap.py) so it is curated, not hand-written
from scratch.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field

import yaml


def edge_key(t1: str, c1: str, t2: str, c2: str) -> tuple:
    """Order-independent identity of a join edge, so a=b and b=a are one edge."""
    return tuple(sorted([(t1, c1), (t2, c2)]))


@dataclass(frozen=True)
class Metric:
    name: str
    agg: str          # SUM, AVG, COUNT, MIN, MAX
    table: str
    column: str
    grain: str = ""
    description: str = ""
    # filters a correct computation of this metric must satisfy, even when the
    # query reaches the metric's column without joining the filtered table
    # (e.g. revenue must exclude soft-deleted orders). (table, column, op) tuples.
    requires_filters: tuple = ()

    def canonical(self) -> str:
        return f"{self.agg.upper()}({self.table}.{self.column})"


@dataclass
class Table:
    name: str
    pk: str
    columns: set[str]
    required_filters: list[tuple[str, str]] = field(default_factory=list)  # (column, op)


@dataclass
class Contract:
    version: str
    tables: dict[str, Table]
    joins: list[tuple[str, str, str, str]]
    metrics: dict[str, Metric] = field(default_factory=dict)

    # ---- construction ----
    @classmethod
    def from_yaml(cls, source) -> "Contract":
        """Load from a file path or a raw YAML string."""
        text = source
        if isinstance(source, (str, os.PathLike)) and os.path.exists(source):
            with open(source) as f:
                text = f.read()
        data = yaml.safe_load(text)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> "Contract":
        # SQL identifiers are case-insensitive unless quoted, so store table/column/pk/
        # join/metric names lowercased. The verifier lowercases unquoted query
        # identifiers to match, so a query using different casing than the schema (e.g.
        # `d.a11` against a column declared `A11`) is not a false SURFACE violation.
        # Operators (`op`), aggregate names, grain/description, and version keep case.
        tables: dict[str, Table] = {}
        for name, spec in (data.get("tables") or {}).items():
            rfs = [(rf["column"].lower(), rf["op"]) for rf in (spec.get("required_filters") or [])]
            tables[name.lower()] = Table(
                name=name.lower(),
                pk=spec["pk"].lower(),
                columns={c.lower() for c in spec["columns"]},
                required_filters=rfs,
            )
        joins = [tuple(str(x).lower() for x in j) for j in (data.get("joins") or [])]
        metrics: dict[str, Metric] = {}
        for m in (data.get("metrics") or []):
            rf = tuple((f["table"].lower(), f["column"].lower(), f["op"])
                       for f in (m.get("requires_filters") or []))
            metrics[m["name"].lower()] = Metric(
                name=m["name"].lower(), agg=m["agg"], table=m["table"].lower(),
                column=m["column"].lower(), grain=m.get("grain", ""),
                description=m.get("description", ""), requires_filters=rf,
            )
        return cls(version=data.get("version", ""), tables=tables, joins=joins, metrics=metrics)

    # ---- graph helpers (ported from the validated spike) ----
    @property
    def declared_edges(self) -> set:
        return {edge_key(*j) for j in self.joins}

    def child_tables_of(self, parent: str) -> set:
        """Tables on the N side of a 1:N with `parent` (joined on the parent's PK)."""
        pk = self.tables[parent].pk
        kids = set()
        for t1, c1, t2, c2 in self.joins:
            if t1 == parent and c1 == pk:
                kids.add(t2)
            if t2 == parent and c2 == pk:
                kids.add(t1)
        return kids

    # ---- identity ----
    def _canonical(self) -> dict:
        return {
            "version": self.version,
            "tables": {
                name: {
                    "pk": t.pk,
                    "columns": sorted(t.columns),
                    "required_filters": [list(rf) for rf in t.required_filters],
                }
                for name, t in sorted(self.tables.items())
            },
            "joins": sorted([list(j) for j in self.joins]),
            "metrics": {
                name: {"agg": m.agg, "table": m.table, "column": m.column, "grain": m.grain,
                       "requires_filters": [list(f) for f in m.requires_filters]}
                for name, m in sorted(self.metrics.items())
            },
        }

    def content_hash(self) -> str:
        return hashlib.sha256(
            json.dumps(self._canonical(), sort_keys=True).encode()
        ).hexdigest()

    def version_label(self) -> str:
        return self.version or f"sha256:{self.content_hash()[:8]}"
