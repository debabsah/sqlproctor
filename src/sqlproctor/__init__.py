"""sqlproctor: deterministic semantic verification for agent-generated SQL.

A query can parse, execute, and still be wrong. sqlproctor checks a query against
a declared semantic contract (surface, join paths, mandatory predicates, fan-out,
metric grain) before it runs, and returns structured violations an agent can
self-correct on.
"""

from .contract import Contract, Metric, Table
from .verifier import verify
from .violation import Verdict, Violation

__all__ = ["Contract", "Metric", "Table", "verify", "Verdict", "Violation"]
