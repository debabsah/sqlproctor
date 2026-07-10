"""The six cases promoted from the validated spike, plus multi-violation and
read-only coverage. Each asserts the exact set of violation kinds.
"""

import pytest

from sqlproctor.verifier import verify


def kinds(sql, contract):
    return {v.kind for v in verify(sql, contract).violations}


# --- the six spike cases -----------------------------------------------------

Q1_CLEAN = """
SELECT c.region, SUM(oi.net_amount) AS revenue
FROM orders o
JOIN order_items oi ON o.order_id = oi.order_id
JOIN customers c ON o.customer_id = c.customer_id
WHERE o.deleted_at IS NULL
GROUP BY c.region
"""

Q2_HALLUCINATED = """
SELECT SUM(o.total_amount) AS total
FROM orders o
WHERE o.deleted_at IS NULL
"""

Q3_MISSING_FILTER = """
SELECT c.region, SUM(oi.net_amount) AS revenue
FROM orders o
JOIN order_items oi ON o.order_id = oi.order_id
JOIN customers c ON o.customer_id = c.customer_id
GROUP BY c.region
"""

Q4_FAN_OUT = """
SELECT SUM(oi.net_amount) AS revenue
FROM orders o
JOIN order_items oi ON o.order_id = oi.order_id
JOIN shipments s ON o.order_id = s.order_id
WHERE o.deleted_at IS NULL
"""

Q5_WRONG_JOIN = """
SELECT c.region, COUNT(*)
FROM orders o
JOIN customers c ON o.order_id = c.customer_id
WHERE o.deleted_at IS NULL
GROUP BY c.region
"""

Q6_NESTED_CTE_CLEAN = """
WITH active_orders AS (
    SELECT o.order_id, o.customer_id
    FROM orders o
    WHERE o.deleted_at IS NULL
),
item_totals AS (
    SELECT ao.customer_id, SUM(oi.net_amount) AS total
    FROM active_orders ao
    JOIN order_items oi ON ao.order_id = oi.order_id
    GROUP BY ao.customer_id
),
ranked AS (
    SELECT it.customer_id, it.total,
           ROW_NUMBER() OVER (ORDER BY it.total DESC) AS rn
    FROM item_totals it
)
SELECT c.region, r.total
FROM ranked r
JOIN customers c ON r.customer_id = c.customer_id
WHERE r.rn <= 10
"""


@pytest.mark.parametrize("sql,expected", [
    (Q1_CLEAN, set()),
    (Q2_HALLUCINATED, {"SURFACE"}),
    (Q3_MISSING_FILTER, {"REQUIRED_FILTER"}),
    (Q4_FAN_OUT, {"FAN_OUT"}),
    (Q5_WRONG_JOIN, {"JOIN_PATH"}),
    (Q6_NESTED_CTE_CLEAN, set()),
])
def test_spike_cases(sql, expected, contract):
    assert kinds(sql, contract) == expected


def test_clean_query_is_ok(contract):
    assert verify(Q1_CLEAN, contract).ok is True


def test_violations_carry_fragment_and_fix(contract):
    v = next(x for x in verify(Q4_FAN_OUT, contract).violations if x.kind == "FAN_OUT")
    assert v.fragment  # points at the offending aggregate
    assert v.suggested_fix
    assert "inflated" in v.message


def test_multiple_violations_reported_together(contract):
    # hallucinated column AND a wrong-key join AND missing soft-delete filter
    sql = """
    SELECT c.region, SUM(o.total_amount) AS total
    FROM orders o
    JOIN customers c ON o.order_id = c.customer_id
    GROUP BY c.region
    """
    found = kinds(sql, contract)
    assert {"SURFACE", "JOIN_PATH", "REQUIRED_FILTER"} <= found


def test_read_only_guard_blocks_writes(contract):
    for sql in ["DELETE FROM orders", "DROP TABLE orders", "UPDATE orders SET status = 'x'"]:
        assert kinds(sql, contract) == {"READ_ONLY"}


def test_parse_error_is_a_violation(contract):
    assert kinds("SELECT FROM WHERE", contract) == {"PARSE"}
