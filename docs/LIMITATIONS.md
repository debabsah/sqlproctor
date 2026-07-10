# Known limitations (V1)

sqlproctor's verdicts are **structural facts about a query relative to a declared
contract**, not a ground-truth correctness oracle. A `verified` result means
"sqlproctor found no contract violation," not "this number is right." The contract
only catches what it models. These are documented honestly rather than discovered
later. **#1 (CTE fan-out) has since been CLOSED**; #2 and #3 remain V2 targets.

They were surfaced empirically by running models through `demo/live_eval.py` against
the retail and SaaS demo warehouses. See `results/RESULTS.md` and `results/transcripts/`.

## 1. Fan-out laundered through a CTE grain (CLOSED 2026-07-09)

**Was:** a fan-out hidden behind a CTE was not detected. The `FAN_OUT` check looks
for an additive aggregate over a column belonging to a child table while a *sibling*
child of the same parent is also joined. When a child table is pre-aggregated inside
a CTE, that table no longer appears in the outer query, so the outer aggregate read
as "over a CTE" and slipped past `FAN_OUT` (the CTE alias is not a contract table).

Reproducible on the seeded retail DB (`examples/seed.py`), true total quantity of
items sold over active orders = **40,756**:

```sql
-- V1 VERIFIED this (2.48x too high: 101,027). It is now BLOCKED as FAN_OUT.
WITH item_totals AS (
    SELECT order_id, SUM(quantity) AS qty FROM order_items GROUP BY order_id
)
SELECT s.carrier, SUM(it.qty) AS total_quantity
FROM shipments s
JOIN orders o        ON s.order_id = o.order_id
JOIN item_totals it  ON o.order_id = it.order_id   -- each order has ~2.5 shipments
WHERE o.deleted_at IS NULL
GROUP BY s.carrier;
```

The CTE collapses `order_items` to one row per order, then the `shipments` join
re-expands each order by its shipment count, so `SUM(it.qty)` is inflated ~2.48x.

**Not a weak-model artifact.** In the 2026-07-09 frontier runs (effort high), Claude
Opus 4.8 and Gemini 3.1 Pro *independently* produced this identical 2.48x laundered
answer and sqlproctor approved both; GPT-5.5 produced a defensible ~1.16x form
(deduplicating carriers per order) and GLM-5.2 held the line, emitting no number.
That two of four frontier models walked into the same hole is what made closing it
the priority.

**Fix (`_check_fan_out_cte` in `verifier.py`):** resolve a single-child
pre-aggregation CTE (FROM exactly one child table, with a `GROUP BY` and an additive
aggregate) to its underlying child, and flag a `SUM` over that CTE when a **raw**
(un-aggregated) sibling child of the same parent is joined at the outer level. The
"raw sibling" requirement is deliberately conservative: it blocks the launder above
(raw `shipments`), but does **not** flag the legitimate pattern where *both* children
are pre-aggregated to parent grain in their own CTEs (no raw multiplier), and it
leaves GPT-5.5's carrier-deduplicated form alone. A false positive (blocking a correct
query) would be worse than the documented false negative was, so the fix errs toward
under-flagging. Pinned by `tests/test_cte_fan_out.py` (the block plus two
false-positive guards). Residual: the `METRIC` check still skips CTE-resolved columns,
but a metric laundered with a raw sibling is now caught by this `FAN_OUT` path.

## 2. Metric bypass via a different column

**A metric-shaped answer computed off a different column is not matched.** The
`METRIC` check keys on the metric's declared `(table, column)` and on projections
aliased with the metric's name. A query that sums a *different* column but that a
human would read as the same business metric is not caught.

Observed on the SaaS demo: for *"billed revenue by payment method"* the model wrote
`SELECT method, SUM(amount) AS billed_revenue FROM payments GROUP BY method`, which
sqlproctor verified. But `billed_revenue` is contractually `SUM(invoice_line_items.amount)`
over non-voided invoices; `SUM(payments.amount)` is money *collected* (and includes
payments against voided invoices), a different quantity wearing the metric's name.

**Nuance: the hole is specifically the *unqualified* column form.** `_check_metric`
already flags the mislabel when the summed column is table-qualified: written as
`SUM(p.amount) AS billed_revenue FROM payments p`, sqlproctor resolves the column to
`payments`, sees `payments != invoice_line_items`, and raises `METRIC`. The escape is
the unqualified `SUM(amount)`: with no table prefix the alias map cannot resolve the
column (`amap.get("")` is `None`), so the check hits the same `src is None` bail-out
(`verifier.py:298`) that legitimately exists for columns resolved *through a CTE* (see
#1's residual), and passes. So the residual false negative is narrow: an aggregate
labeled with a declared metric's name, over an unqualified column, in a single-table
`FROM` (exactly what a model writes when there is only one table to disambiguate).

**V2 consideration:** when a projection is aliased as a declared metric name and
aggregates an *unqualified* column, resolve it against the query's real table set
(one table in `FROM` leaves one candidate) before bailing, and flag if it is not the
metric's column. The care is distinguishing "unqualified but resolvable to a real
table" (flag) from "resolves through a CTE" (must keep bailing, per #1).

## 3. `REQUIRED_FILTER` only enforces `IS NULL`

V1 only models `IS NULL` required-filter predicates (`verifier.py:113`). A non-null
predicate (e.g. `status != 'void'`) cannot be declared as a required filter without
firing on every query that touches the table, correct ones included. As a result the
demo contracts model soft-deletes as nullable timestamp columns (`deleted_at`,
`voided_at`, `canceled_at`, all `IS NULL`) rather than status strings.

**Nuance: a non-null required filter is *unusable*, not merely unenforced.** The `else`
branch hard-codes `satisfied = False` (`verifier.py:122`), so a declared non-null
predicate is treated as never-satisfied. Declaring `status != 'void'` therefore raises
`REQUIRED_FILTER` even on a query that correctly writes `WHERE status != 'void'`: a 100%
false-positive rate that bricks the table. This is why the demo cannot model
status-string conventions at all and remodels every soft-delete as a nullable
timestamp.

**Nuance: this is an *expressiveness* gap, not a *protection* gap.** The common
soft-delete convention has no hole today: modeled as a nullable timestamp it is fully
enforced. #3 only bites when the warehouse uses a status-string or boolean-flag
convention (`status = 'active'`, `is_deleted = false`) that cannot be remodeled as
`IS NULL`, i.e. a schema you do not control. So its value is realized on a
real-warehouse dogfood, not on the synthetic demo where every convention was chosen to
be null-shaped.

**V2 target:** support a small set of comparison predicates (`=`, `!=`, `IN`) in
required filters, matched against the query's `WHERE`/`ON` conditions. Design care:
`status != 'void'`, `status <> 'void'`, and `status NOT IN ('void')` are semantically
equal but syntactically different, so matching must be on meaning, not token shape.
