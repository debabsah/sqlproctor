"""The verifier: parse a query and check it against a contract. Deterministic.

No LLM judges an LLM here. Every finding is a structural fact about the query
relative to declared truth. The four graph checks are ported from the validated
spike; metric-conformance and the read-only guard are added for V1.
"""

from __future__ import annotations

import difflib

import sqlglot
from sqlglot import exp

from .contract import Contract, edge_key
from .violation import Verdict, Violation

# sqlglot dialect used to parse. Postgres is a permissive superset that also reads
# the DuckDB syntax used in the demo; override per call for other warehouses.
DEFAULT_DIALECT = "postgres"

_READ_ONLY_ROOTS = (exp.Select, exp.Union)


def verify(sql: str, contract: Contract, dialect: str = DEFAULT_DIALECT) -> Verdict:
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except sqlglot.errors.ParseError as e:
        return Verdict.of([Violation("PARSE", str(e).splitlines()[0])])
    if tree is None:
        return Verdict.of([Violation("PARSE", "empty statement")])

    _normalize_identifiers(tree)

    # Read-only guard first: the other checks assume SELECT structure, and a write
    # should be refused outright regardless of what else is true about it.
    if not isinstance(tree, _READ_ONLY_ROOTS):
        return Verdict.of([Violation(
            "READ_ONLY",
            f"only read-only SELECT queries are permitted (got {type(tree).__name__})",
            fragment=tree.sql()[:120],
        )])

    amap, cte_names = _alias_map(tree, contract)
    violations: list[Violation] = []
    violations += _check_surface(tree, contract, amap, cte_names)
    violations += _check_join_path(tree, contract, amap)
    violations += _check_required_filter(tree, contract, amap)
    violations += _check_fan_out(tree, contract, amap)
    violations += _check_fan_out_cte(tree, contract, amap, cte_names)

    outer = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    if outer is not None:
        violations += _check_metric(outer, contract, amap)
    violations += _check_metric_filters(tree, contract, amap)

    return Verdict.of(violations)


def _normalize_identifiers(tree):
    """SQL folds unquoted identifiers case-insensitively; do the same so contract checks
    match regardless of the casing a model uses (`d.a11` vs a declared `A11`). Quoted
    identifiers keep their case, since a quoted name IS case-sensitive."""
    for ident in tree.find_all(exp.Identifier):
        if not ident.args.get("quoted") and isinstance(ident.this, str):
            ident.set("this", ident.this.lower())
    return tree


def _alias_map(tree, contract):
    """alias/name -> real contract table (CTE names/aliases excluded)."""
    cte_names = {cte.alias_or_name for cte in tree.find_all(exp.CTE)}
    amap = {}
    for t in tree.find_all(exp.Table):
        if t.name in contract.tables and t.name not in cte_names:
            amap[t.alias_or_name] = t.name
        elif t.name in cte_names:
            cte_names.add(t.alias_or_name)  # e.g. FROM ranked r -> `r` is a CTE ref
    return amap, cte_names


def _check_surface(tree, contract, amap, cte_names):
    out = []
    for col in tree.find_all(exp.Column):
        tbl = col.table
        if not tbl:
            continue  # unqualified: cannot attribute to a table, so cannot judge
        src = amap.get(tbl)
        if tbl not in amap and tbl not in cte_names:
            out.append(Violation("SURFACE", f"unknown table or alias '{tbl}'", col.sql()))
        elif src and col.name not in contract.tables[src].columns:
            near = difflib.get_close_matches(col.name, sorted(contract.tables[src].columns), n=1)
            fix = f"did you mean {src}.{near[0]}?" if near else ""
            out.append(Violation("SURFACE", f"column {src}.{col.name} does not exist", col.sql(), fix))
    return out


def _check_join_path(tree, contract, amap):
    out = []
    edges = contract.declared_edges
    for join in tree.find_all(exp.Join):
        on = join.args.get("on")
        if on is None:
            continue
        for eq in on.find_all(exp.EQ):
            left, right = eq.left, eq.right
            if isinstance(left, exp.Column) and isinstance(right, exp.Column):
                lt, rt = amap.get(left.table), amap.get(right.table)
                if lt and rt and edge_key(lt, left.name, rt, right.name) not in edges:
                    out.append(Violation(
                        "JOIN_PATH",
                        f"{lt}.{left.name} = {rt}.{right.name} is not a declared relationship",
                        eq.sql(),
                        "join only along declared foreign keys",
                    ))
    return out


def _check_required_filter(tree, contract, amap):
    out = []
    real_tables = set(amap.values())
    is_null_nodes = [n for n in tree.find_all(exp.Is) if isinstance(n.expression, exp.Null)]
    for tname in sorted(real_tables):
        for col, op in contract.tables[tname].required_filters:
            if op.upper() == "IS NULL":
                satisfied = any(
                    isinstance(n.this, exp.Column)
                    and n.this.name == col
                    and amap.get(n.this.table, tname) == tname
                    and not isinstance(n.parent, exp.Not)
                    for n in is_null_nodes
                )
            else:
                satisfied = False  # only IS NULL predicates are modelled in V1
            if not satisfied:
                out.append(Violation(
                    "REQUIRED_FILTER",
                    f"missing mandatory predicate {tname}.{col} {op}",
                    suggested_fix=f"add WHERE {tname}.{col} {op}",
                ))
    return out


def _check_fan_out(tree, contract, amap):
    out = []
    real_tables = set(amap.values())
    parents = {p for p in real_tables if contract.child_tables_of(p) & real_tables}
    for parent in sorted(parents):
        siblings = contract.child_tables_of(parent) & real_tables
        if len(siblings) < 2:
            continue
        for agg in tree.find_all(exp.Sum, exp.Avg, exp.Count):
            for col in agg.find_all(exp.Column):
                src = amap.get(col.table)
                if src in siblings:
                    out.append(Violation(
                        "FAN_OUT",
                        f"{agg.sql_name()}({src}.{col.name}) computed while "
                        f"{' and '.join(sorted(siblings))} are both joined via {parent}: "
                        f"rows are multiplied, the result will be inflated",
                        agg.sql(),
                        f"aggregate {src} in a subquery/CTE before joining the other "
                        f"{parent} child, or drop the extra join",
                    ))
    return out


def _outer_real_tables(root, contract, cte_names):
    """Real contract tables joined at the OUTER query level, excluding CTE references
    and anything inside a CTE body or a nested subquery. Lets us tell a raw
    many-per-parent child join apart from a child pre-aggregated inside a CTE. Uses an
    ancestor walk (not arg-key names) to stay robust across sqlglot versions."""
    def nested(node):
        p = node.parent
        while p is not None:
            if isinstance(p, exp.CTE) or (isinstance(p, exp.Select) and p is not root):
                return True
            p = p.parent
        return False
    out = {}
    for t in root.find_all(exp.Table):
        if t.name in contract.tables and t.name not in cte_names and not nested(t):
            out[t.alias_or_name] = t.name
    return out


def _check_fan_out_cte(tree, contract, amap, cte_names):
    """Fan-out laundered through a CTE. A child table pre-aggregated to its parent's
    grain inside a CTE, then summed while a RAW sibling child of the same parent is
    joined at the outer level, still re-inflates - but the flat fan-out check misses
    it because the CTE alias is not a contract table. We resolve single-child
    pre-aggregation CTEs to their underlying child and require a raw (un-aggregated)
    sibling at the outer level, so the legitimate "both children pre-aggregated to
    parent grain" pattern does NOT trip a false positive.
    """
    # CTEs that are a single-child pre-aggregation: FROM exactly one real child table,
    # with a GROUP BY and an additive aggregate. Map CTE name -> underlying child.
    cte_child = {}
    for cte in tree.find_all(exp.CTE):
        body = cte.this
        srcs = {t.name for t in body.find_all(exp.Table) if t.name in contract.tables}
        if len(srcs) != 1:
            continue
        if body.find(exp.Group) is None or body.find(exp.Sum, exp.Count, exp.Avg) is None:
            continue
        cte_child[cte.alias_or_name] = next(iter(srcs))
    if not cte_child:
        return []

    alias_child = {}  # outer alias of the CTE ref -> underlying child (FROM cte c -> c)
    for t in tree.find_all(exp.Table):
        if t.name in cte_child:
            alias_child[t.alias_or_name] = cte_child[t.name]

    root = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    if root is None:
        return []
    outer_real = set(_outer_real_tables(root, contract, cte_names).values())

    out, reported = [], set()
    for cte_alias, child_a in alias_child.items():
        for parent in contract.tables:
            kids = contract.child_tables_of(parent)
            if child_a not in kids or parent not in outer_real:
                continue
            raw_siblings = (kids & outer_real) - {child_a}
            if not raw_siblings:
                continue
            for agg in root.find_all(exp.Sum, exp.Avg, exp.Count):
                if not any(c.table == cte_alias for c in agg.find_all(exp.Column)):
                    continue
                key = (parent, child_a, agg.sql())
                if key in reported:
                    continue
                reported.add(key)
                sib = " and ".join(sorted(raw_siblings))
                out.append(Violation(
                    "FAN_OUT",
                    f"{agg.sql_name()} over a CTE that pre-aggregates {child_a} to "
                    f"{parent} grain, while raw {sib} is joined via {parent}: the CTE "
                    f"rows are multiplied, the result will be inflated",
                    agg.sql(),
                    f"aggregate {sib} to {parent} grain in its own CTE before joining, "
                    f"or drop the extra join",
                ))
    return out


def _resolve_col_table(col, contract, amap):
    """Real table a column belongs to. Resolves unqualified columns to the single
    real table that declares them (None if ambiguous)."""
    if col.table:
        return amap.get(col.table)
    candidates = [t for t in set(amap.values()) if col.name in contract.tables[t].columns]
    return candidates[0] if len(candidates) == 1 else None


def _check_metric_filters(tree, contract, amap):
    """A query that computes a metric's aggregate (by shape, regardless of the
    output alias) must satisfy that metric's required filters. This catches
    revenue summed straight off a child table, bypassing the filtered parent it
    should be computed through. Only fires when the required-filter table is
    absent from the query; the present-but-unfiltered case is REQUIRED_FILTER's.
    """
    out = []
    real_tables = set(amap.values())
    for metric in contract.metrics.values():
        if not metric.requires_filters:
            continue
        for agg in tree.find_all(exp.AggFunc):
            if agg.sql_name().upper() != metric.agg.upper():
                continue
            arg = agg.this
            if not isinstance(arg, exp.Column):
                continue
            if _resolve_col_table(arg, contract, amap) != metric.table or arg.name != metric.column:
                continue
            for ftable, fcol, fop in metric.requires_filters:
                if ftable not in real_tables:
                    out.append(Violation(
                        "METRIC",
                        f"{metric.name} ({metric.canonical()}) is computed without joining "
                        f"{ftable}, so the required filter {ftable}.{fcol} {fop} is not applied "
                        f"and excluded rows are counted",
                        agg.sql(),
                        f"compute {metric.name} through {ftable} with {ftable}.{fcol} {fop} applied",
                    ))
            break  # one report per metric is enough
    return out


def _check_metric(select, contract, amap):
    out = []
    for proj in select.expressions:
        metric = contract.metrics.get(proj.alias_or_name)
        if metric is None:
            continue
        agg = proj.find(exp.AggFunc)
        if agg is None:
            out.append(Violation(
                "METRIC",
                f"'{metric.name}' is a declared metric but this projection is not aggregated",
                proj.sql(),
                f"use {metric.canonical()} AS {metric.name}",
            ))
            continue
        arg = agg.this
        if isinstance(arg, exp.Column):
            src = amap.get(arg.table)
            if src is None:
                continue  # aggregated column resolves through a CTE; cannot verify grain (V1 limit)
            if (agg.sql_name().upper() == metric.agg.upper()
                    and src == metric.table and arg.name == metric.column):
                continue  # conforms
        out.append(Violation(
            "METRIC",
            f"projection aliased '{metric.name}' does not match the declared metric "
            f"{metric.canonical()}",
            agg.sql(),
            f"use {metric.canonical()} AS {metric.name}",
        ))
    return out
