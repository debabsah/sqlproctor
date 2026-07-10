"""SQL identifiers are case-insensitive unless quoted. Running sqlproctor on the BIRD
financial benchmark surfaced a false positive: a correct query using `d.a11` was blocked
because the column was declared `A11`. These pin that casing no longer matters, while
genuinely wrong names and the structural traps are still caught case-insensitively.
"""

import pathlib

import pytest

from sqlproctor.contract import Contract
from sqlproctor.verifier import verify

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture
def retail() -> Contract:
    return Contract.from_yaml(str(ROOT / "contracts" / "retail.yml"))


def _kinds(sql, c):
    return [v.kind for v in verify(sql, c).violations]


def test_uppercase_correct_query_is_clean(retail):
    sql = ("SELECT SUM(OI.NET_AMOUNT) FROM ORDERS O "
           "JOIN ORDER_ITEMS OI ON O.ORDER_ID = OI.ORDER_ID WHERE O.DELETED_AT IS NULL")
    assert _kinds(sql, retail) == []


def test_uppercase_fan_out_is_still_caught(retail):
    sql = ("SELECT SUM(OI.NET_AMOUNT) FROM ORDERS O "
           "JOIN ORDER_ITEMS OI ON O.ORDER_ID = OI.ORDER_ID "
           "JOIN SHIPMENTS S ON O.ORDER_ID = S.ORDER_ID WHERE O.DELETED_AT IS NULL")
    assert "FAN_OUT" in _kinds(sql, retail)


def test_genuinely_missing_column_is_still_surface(retail):
    assert "SURFACE" in _kinds("SELECT o.nonsense_col FROM orders o", retail)


def test_contract_identifiers_are_stored_lowercased():
    c = Contract.from_yaml(
        "version: t\ntables:\n  T:\n    pk: ID\n    columns: [ID, Amount]\njoins: []\n")
    assert set(c.tables) == {"t"}
    assert c.tables["t"].pk == "id"
    assert c.tables["t"].columns == {"id", "amount"}
