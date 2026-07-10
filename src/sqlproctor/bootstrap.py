"""Generate a starter contract from a live database.

The tedious surface (tables, columns, primary keys, the foreign-key join graph)
is read straight from the catalog, so you never hand-write it. What the schema
cannot express, the mandatory filters and the metric definitions, is emitted as
commented TODO stubs for a human to curate.

DuckDB in V1; the two catalog queries generalize to any information_schema.
"""

from __future__ import annotations

import duckdb

from .contract import Contract


def _introspect(con):
    """Returns (tables: {name: {'pk', 'columns'}}, joins: [(reft, refc, t, c)])."""
    base = {
        r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' AND table_type = 'BASE TABLE'"
        ).fetchall()
    }

    tables: dict[str, dict] = {}
    for tname, cname in con.execute(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'main' ORDER BY table_name, ordinal_position"
    ).fetchall():
        if tname in base:
            tables.setdefault(tname, {"pk": None, "columns": []})["columns"].append(cname)

    for tname, ccols in con.execute(
        "SELECT table_name, constraint_column_names FROM duckdb_constraints() "
        "WHERE constraint_type = 'PRIMARY KEY'"
    ).fetchall():
        if tname in tables and ccols:
            tables[tname]["pk"] = ccols[0]

    joins = []
    for tname, ccols, rtable, rcols in con.execute(
        "SELECT table_name, constraint_column_names, referenced_table, referenced_column_names "
        "FROM duckdb_constraints() WHERE constraint_type = 'FOREIGN KEY'"
    ).fetchall():
        if ccols and rcols and tname in base:
            # store as (referenced=parent, child) so the parent's PK is the first pair
            joins.append((rtable, rcols[0], tname, ccols[0]))

    return tables, joins


def bootstrap_to_contract(con, version: str = "v1") -> Contract:
    tables, joins = _introspect(con)
    data = {
        "version": version,
        "tables": {t: {"pk": spec["pk"], "columns": spec["columns"]}
                   for t, spec in tables.items()},
        "joins": [list(j) for j in joins],
        "metrics": [],
    }
    return Contract.from_dict(data)


def bootstrap_contract(con, version: str = "v1") -> str:
    """Human-facing YAML with commented stubs for the curated delta."""
    tables, joins = _introspect(con)
    lines = [f"version: {version}", "", "tables:"]
    for tname in sorted(tables):
        spec = tables[tname]
        lines += [
            f"  {tname}:",
            f"    pk: {spec['pk']}",
            f"    columns: [{', '.join(spec['columns'])}]",
            "    # required_filters:   # TODO curate: predicates every correct query must include",
            '    #   - {column: <col>, op: "IS NULL"}',
        ]
    lines += ["", "joins:"]
    for reft, refc, t, c in joins:
        lines.append(f"  - [{reft}, {refc}, {t}, {c}]")
    lines += [
        "",
        "# metrics:   # TODO curate: business metrics and their grain",
        "#   - {name: <metric>, agg: SUM, table: <table>, column: <col>, grain: <grain>}",
    ]
    return "\n".join(lines) + "\n"


def bootstrap_from_path(db_path: str, version: str = "v1") -> str:
    con = duckdb.connect(db_path, read_only=True)
    try:
        return bootstrap_contract(con, version)
    finally:
        con.close()
