"""The second demo contract (SaaS billing) must encode the same failure modes as
retail. Verifier checks are pure (parse + contract), so no DB is needed here."""

import pathlib

import pytest

from sqlproctor.contract import Contract
from sqlproctor.verifier import verify

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture
def saas() -> Contract:
    return Contract.from_yaml(str(ROOT / "contracts" / "saas.yml"))


def _kinds(sql, contract):
    return [v.kind for v in verify(sql, contract).violations]


def test_loads_expected_shape(saas):
    assert set(saas.tables) == {
        "accounts", "subscriptions", "invoices",
        "invoice_line_items", "payments", "usage_events",
    }
    assert ("voided_at", "IS NULL") in saas.tables["invoices"].required_filters
    assert ("canceled_at", "IS NULL") in saas.tables["subscriptions"].required_filters
    assert saas.metrics["billed_revenue"].canonical() == "SUM(invoice_line_items.amount)"
    assert saas.version_label() == "saas-v1"


def test_fan_out_via_payments_is_caught(saas):
    sql = ("SELECT SUM(li.amount) FROM invoices i "
           "JOIN invoice_line_items li ON i.invoice_id=li.invoice_id "
           "JOIN payments p ON i.invoice_id=p.invoice_id WHERE i.voided_at IS NULL")
    assert "FAN_OUT" in _kinds(sql, saas)


def test_revenue_bypassing_invoices_is_caught(saas):
    # billed_revenue summed straight off the line-item table, skipping the voided filter
    assert "METRIC" in _kinds("SELECT SUM(amount) AS billed_revenue FROM invoice_line_items", saas)


def test_missing_void_filter_is_caught(saas):
    sql = ("SELECT SUM(li.amount) FROM invoices i "
           "JOIN invoice_line_items li ON i.invoice_id=li.invoice_id")
    assert "REQUIRED_FILTER" in _kinds(sql, saas)


def test_undeclared_usage_subscription_join_is_caught(saas):
    sql = ("SELECT COUNT(*) FROM usage_events ue "
           "JOIN subscriptions s ON ue.account_id=s.account_id WHERE s.canceled_at IS NULL")
    assert "JOIN_PATH" in _kinds(sql, saas)


def test_correct_queries_verify_clean(saas):
    good_rev = ("SELECT SUM(li.amount) FROM invoices i "
                "JOIN invoice_line_items li ON i.invoice_id=li.invoice_id WHERE i.voided_at IS NULL")
    good_mrr = "SELECT SUM(mrr_amount) AS mrr FROM subscriptions WHERE canceled_at IS NULL"
    assert _kinds(good_rev, saas) == []
    assert _kinds(good_mrr, saas) == []
