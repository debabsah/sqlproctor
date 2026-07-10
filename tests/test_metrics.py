"""Metric-conformance: a projection aliased as a declared metric must match the
declared aggregate, table, and column. Documented V1 limit: only mistakes aliased
as the metric name are caught.
"""

from sqlproctor.verifier import verify


def kinds(sql, contract):
    return {v.kind for v in verify(sql, contract).violations}


BASE = ("FROM orders o "
        "JOIN order_items oi ON o.order_id = oi.order_id "
        "WHERE o.deleted_at IS NULL")


def test_correct_metric_conforms(contract):
    sql = f"SELECT SUM(oi.net_amount) AS revenue {BASE}"
    assert "METRIC" not in kinds(sql, contract)


def test_wrong_column_flagged(contract):
    sql = f"SELECT SUM(oi.quantity) AS revenue {BASE}"
    assert "METRIC" in kinds(sql, contract)


def test_wrong_aggregate_flagged(contract):
    sql = f"SELECT AVG(oi.net_amount) AS revenue {BASE}"
    assert "METRIC" in kinds(sql, contract)


def test_unaggregated_metric_flagged(contract):
    sql = f"SELECT oi.net_amount AS revenue {BASE}"
    assert "METRIC" in kinds(sql, contract)


def test_expression_argument_flagged(contract):
    sql = f"SELECT SUM(oi.net_amount * oi.quantity) AS revenue {BASE}"
    assert "METRIC" in kinds(sql, contract)


def test_metric_under_cte_is_not_falsely_flagged(contract):
    # revenue is computed off a CTE column; grain cannot be verified, so V1 must
    # NOT flag it (documented limitation, not a false positive).
    sql = """
    WITH t AS (
        SELECT oi.net_amount AS amt
        FROM orders o
        JOIN order_items oi ON o.order_id = oi.order_id
        WHERE o.deleted_at IS NULL
    )
    SELECT SUM(t.amt) AS revenue FROM t
    """
    assert "METRIC" not in kinds(sql, contract)


def test_metric_fix_names_the_declared_expression(contract):
    sql = f"SELECT SUM(oi.quantity) AS revenue {BASE}"
    v = next(x for x in verify(sql, contract).violations if x.kind == "METRIC")
    assert "SUM(order_items.net_amount)" in v.suggested_fix


# --- metric-required-filter: revenue computed off the child table, bypassing the
#     filtered parent (the exact GLM 5.2 "total revenue" false negative) ---------

def test_revenue_summed_off_child_table_bypassing_filter_is_caught(contract):
    # sums order_items directly (unqualified column), never joins orders, so the
    # active-orders filter never applies -> counts soft-deleted orders' revenue.
    sql = "SELECT SUM(net_amount) AS total_revenue FROM order_items"
    v = next((x for x in verify(sql, contract).violations if x.kind == "METRIC"), None)
    assert v is not None
    assert "orders" in v.message and "deleted_at" in v.message


def test_revenue_through_filtered_orders_is_clean(contract):
    sql = ("SELECT SUM(oi.net_amount) AS total_revenue FROM orders o "
           "JOIN order_items oi ON o.order_id = oi.order_id WHERE o.deleted_at IS NULL")
    assert "METRIC" not in kinds(sql, contract)


def test_present_but_unfiltered_orders_stays_required_filter_not_double_reported(contract):
    # orders IS joined but not filtered -> REQUIRED_FILTER owns it; the metric-filter
    # check must NOT also fire (no double report).
    sql = ("SELECT SUM(oi.net_amount) AS revenue FROM orders o "
           "JOIN order_items oi ON o.order_id = oi.order_id")
    found = kinds(sql, contract)
    assert "REQUIRED_FILTER" in found
    assert "METRIC" not in found
