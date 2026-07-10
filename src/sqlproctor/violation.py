"""Structured verification results.

Violation kinds:
  SURFACE          - references a table/column that does not exist in the contract
  JOIN_PATH        - joins along an edge the contract does not declare
  REQUIRED_FILTER  - omits a mandatory predicate (e.g. soft-delete exclusion)
  FAN_OUT          - additive aggregate over rows multiplied by a sibling join
  METRIC           - a projection aliased as a declared metric does not match its grain
  READ_ONLY        - statement is not a read-only SELECT
  PARSE            - query could not be parsed
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Violation:
    kind: str
    message: str
    fragment: str = ""        # the offending SQL fragment, for the agent to locate
    suggested_fix: str = ""   # a concrete correction the agent can apply

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Verdict:
    ok: bool
    violations: list[Violation] = field(default_factory=list)

    @classmethod
    def of(cls, violations: list[Violation]) -> "Verdict":
        return cls(ok=len(violations) == 0, violations=violations)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "violations": [v.to_dict() for v in self.violations]}
