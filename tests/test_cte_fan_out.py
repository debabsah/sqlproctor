"""Regression tests for the CTE-laundered fan-out fix (was docs/LIMITATIONS.md #1).

A child table pre-aggregated in a CTE, then summed while a RAW sibling child of the
same parent is joined at the outer level, re-inflates. V1 missed it. These pin that
it is now caught AND - the reason the fix is conservative - that it does NOT over-block
the legitimate "both children pre-aggregated to parent grain" pattern (a false
positive would be worse than the documented false negative was).
"""

from sqlproctor.verifier import verify


def _kinds(sql, contract):
    return [v.kind for v in verify(sql, contract).violations]


def test_cte_laundered_fan_out_is_blocked(contract):
    # order_items pre-aggregated in a CTE, then summed while raw shipments (a sibling
    # child of orders) is joined at the outer level -> ~2.48x inflation. Now caught.
    # This is the exact shape Claude Opus 4.8 and Gemini 3.1 Pro produced.
    sql = """
        WITH item_totals AS (
            SELECT order_id, SUM(quantity) AS qty FROM order_items GROUP BY order_id
        )
        SELECT s.carrier, SUM(it.qty) AS total_quantity
        FROM shipments s
        JOIN orders o       ON s.order_id = o.order_id
        JOIN item_totals it ON o.order_id = it.order_id
        WHERE o.deleted_at IS NULL
        GROUP BY s.carrier
    """
    assert "FAN_OUT" in _kinds(sql, contract)


def test_both_children_preaggregated_is_not_a_false_positive(contract):
    # Both children aggregated to order grain in their own CTEs, then joined 1:1 to
    # orders. No raw many-per-order sibling, so no inflation - must stay clean.
    sql = """
        WITH oi AS (SELECT order_id, SUM(quantity) qty FROM order_items GROUP BY order_id),
             sh AS (SELECT order_id, COUNT(*) c FROM shipments GROUP BY order_id)
        SELECT SUM(oi.qty)
        FROM orders o
        JOIN oi ON o.order_id = oi.order_id
        JOIN sh ON o.order_id = sh.order_id
        WHERE o.deleted_at IS NULL
    """
    assert _kinds(sql, contract) == []


def test_cte_preagg_joined_to_parent_only_is_clean(contract):
    # The correct form: pre-aggregate order_items, join to orders only, no sibling.
    sql = """
        WITH oi AS (SELECT order_id, SUM(quantity) qty FROM order_items GROUP BY order_id)
        SELECT SUM(oi.qty)
        FROM oi JOIN orders o ON o.order_id = oi.order_id
        WHERE o.deleted_at IS NULL
    """
    assert _kinds(sql, contract) == []
