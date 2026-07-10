import pathlib

import pytest

from sqlproctor.contract import Contract

ROOT = pathlib.Path(__file__).resolve().parents[1]
RETAIL = str(ROOT / "contracts" / "retail.yml")


def test_loads_expected_shape(contract):
    assert set(contract.tables) == {"orders", "order_items", "customers", "shipments"}
    assert contract.tables["orders"].pk == "order_id"
    assert ("deleted_at", "IS NULL") in contract.tables["orders"].required_filters
    assert len(contract.joins) == 3
    assert contract.metrics["revenue"].canonical() == "SUM(order_items.net_amount)"


def test_version_label(contract):
    assert contract.version_label() == "v1"


def test_content_hash_is_stable_across_reload():
    a = Contract.from_yaml(RETAIL)
    b = Contract.from_yaml(RETAIL)
    assert a.content_hash() == b.content_hash()


def test_content_hash_changes_with_content():
    base = Contract.from_yaml(RETAIL)
    mutated = Contract.from_yaml(RETAIL)
    mutated.tables["orders"].columns.add("secret_column")
    assert base.content_hash() != mutated.content_hash()


def test_unversioned_contract_falls_back_to_hash():
    c = Contract.from_yaml(
        "tables: {orders: {pk: order_id, columns: [order_id]}}\njoins: []\n"
    )
    assert c.version_label().startswith("sha256:")


def test_from_string_and_from_path_agree():
    with open(RETAIL) as f:
        from_text = Contract.from_yaml(f.read())
    from_path = Contract.from_yaml(RETAIL)
    assert from_text.content_hash() == from_path.content_hash()


def test_malformed_yaml_raises():
    with pytest.raises(Exception):
        Contract.from_yaml("tables: [unclosed\n")
