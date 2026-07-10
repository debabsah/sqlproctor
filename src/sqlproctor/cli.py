"""sqlproctor CLI. `check` today; `bootstrap` and `serve` are added alongside
those modules.
"""

from __future__ import annotations

import argparse
import sys

from .contract import Contract
from .verifier import verify


def cmd_check(args) -> int:
    contract = Contract.from_yaml(args.contract)
    with open(args.sql_file) as f:
        sql = f.read()
    verdict = verify(sql, contract, dialect=args.dialect)
    if verdict.ok:
        print(f"OK  verified against contract {contract.version_label()}")
        return 0
    print(f"BLOCKED  {len(verdict.violations)} violation(s) "
          f"against contract {contract.version_label()}", file=sys.stderr)
    for v in verdict.violations:
        print(f"  [{v.kind}] {v.message}", file=sys.stderr)
        if v.suggested_fix:
            print(f"      fix: {v.suggested_fix}", file=sys.stderr)
    return 1


def cmd_bootstrap(args) -> int:
    from .bootstrap import bootstrap_from_path
    print(bootstrap_from_path(args.db, version=args.version), end="")
    return 0


def cmd_serve(args) -> int:
    from .mcp_server import main as serve_main
    serve_main()  # runs the stdio MCP server until the client disconnects
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sqlproctor", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="verify a .sql file against a contract")
    c.add_argument("sql_file")
    c.add_argument("--contract", default="contracts/retail.yml")
    c.add_argument("--dialect", default="postgres")
    c.set_defaults(func=cmd_check)

    b = sub.add_parser("bootstrap", help="generate a starter contract from a database")
    b.add_argument("--db", required=True, help="path to a DuckDB database file")
    b.add_argument("--version", default="v1")
    b.set_defaults(func=cmd_bootstrap)

    s = sub.add_parser("serve", help="run the verifying MCP server (stdio)")
    s.set_defaults(func=cmd_serve)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
