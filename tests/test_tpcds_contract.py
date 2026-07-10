"""The TPC-DS store-channel contract must fire the structural checks on the schema's
own natural traps. Verifier checks are pure (parse + contract), so no DB is needed here;
the numeric reality of the fan-out is proven separately by examples/tpcds_seed.py.
"""

import pathlib

import pytest

from sqlproctor.contract import Contract
from sqlproctor.verifier import verify

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture
def tpcds() -> Contract:
    return Contract.from_yaml(str(ROOT / "contracts" / "tpcds.yml"))


def _kinds(sql, contract):
    return [v.kind for v in verify(sql, contract).violations]


def test_loads_expected_shape(tpcds):
    assert set(tpcds.tables) == {
        "store_sales", "store_returns", "item", "customer", "date_dim", "store",
    }
    # TPC-DS has no soft-delete convention: no required_filters anywhere.
    assert all(t.required_filters == [] for t in tpcds.tables.values())
    assert tpcds.metrics["store_revenue"].canonical() == "SUM(store_sales.ss_net_paid)"
    assert tpcds.version_label() == "tpcds-v1"


def test_fan_out_via_shared_dimension_is_caught(tpcds):
    # store_sales and store_returns are both children of item; summing a sales measure
    # while both are joined via item cross-products each item's sales by its returns.
    sql = ("SELECT SUM(ss.ss_net_paid) FROM item i "
           "JOIN store_sales ss   ON i.i_item_sk = ss.ss_item_sk "
           "JOIN store_returns sr ON i.i_item_sk = sr.sr_item_sk")
    assert "FAN_OUT" in _kinds(sql, tpcds)


def test_direct_fact_to_fact_join_is_caught(tpcds):
    # Joining the two facts directly is not a declared edge (aggregate to a common
    # grain first); the correct pattern goes through a dimension or a pre-aggregation.
    sql = ("SELECT SUM(ss.ss_net_paid) FROM store_sales ss "
           "JOIN store_returns sr ON ss.ss_item_sk = sr.sr_item_sk")
    assert "JOIN_PATH" in _kinds(sql, tpcds)


def test_revenue_off_a_different_column_is_caught(tpcds):
    # store_revenue is SUM(ss_net_paid); reporting ss_ext_sales_price under that name
    # is a different quantity wearing the metric's alias.
    sql = "SELECT SUM(ss.ss_ext_sales_price) AS store_revenue FROM store_sales ss"
    assert "METRIC" in _kinds(sql, tpcds)


def test_hallucinated_column_is_caught(tpcds):
    assert "SURFACE" in _kinds("SELECT SUM(ss.ss_revenue) FROM store_sales ss", tpcds)


def test_correct_queries_verify_clean(tpcds):
    total = "SELECT SUM(ss.ss_net_paid) AS store_revenue FROM store_sales ss"
    by_category = ("SELECT i.i_category, SUM(ss.ss_net_paid) FROM store_sales ss "
                   "JOIN item i ON i.i_item_sk = ss.ss_item_sk GROUP BY i.i_category")
    assert _kinds(total, tpcds) == []
    assert _kinds(by_category, tpcds) == []
