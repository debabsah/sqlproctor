# sqlproctor

Deterministic semantic verification for agent-generated SQL.

Security firewalls stop malicious queries. Nothing stops wrong ones. sqlproctor is the missing piece: it sits between an AI agent and a database, holds a git-versioned semantic contract, and verifies every query the agent writes against that contract before it runs. Violations come back as structured feedback the agent uses to fix its own query. Every query and verdict is written to an append-only ledger.

A query can parse, execute, and still be quietly, confidently wrong: a hallucinated column, a join along the wrong key, a missing soft-delete filter, an aggregate silently inflated by a fan-out join. Those are the failures that reach a dashboard and lose the room. sqlproctor catches them structurally, with no second LLM in the loop.

If your agents run against a usage-billed warehouse (BigQuery, Snowflake, Athena), correctness is not the only win: every query the guard blocks is compute you never pay for, and because fan-outs and unfiltered scans are both the wrong answers and the expensive ones, you stop funding the agent's bad attempts instead of just catching them.

## The five checks

| Check | Catches | Example it blocks |
|-------|---------|-------------------|
| SURFACE | hallucinated tables and columns | `SUM(orders.total_amount)` when that column does not exist |
| JOIN_PATH | joins along an undeclared relationship | `orders.order_id = customers.customer_id` |
| REQUIRED_FILTER | a missing mandatory predicate | revenue query with no `deleted_at IS NULL` |
| FAN_OUT | additive aggregates over multiplied rows | `SUM(order_items.net_amount)` while also joined to `shipments` |
| METRIC | a metric computed off-grain | `AVG(order_items.net_amount) AS revenue` |

All of these parse and execute. All of them return the wrong number. The core is a set of structural checks over the parsed query (built on [sqlglot](https://github.com/tobymao/sqlglot), roughly 30 SQL dialects), so it is deterministic and explainable.

## Quickstart

```bash
pip install -e ".[dev]"
python examples/seed.py          # builds the demo DuckDB (reproducible, fixed seed)
python demo/demo.py              # watch an agent get blocked, correct itself, and pass
```

Check a single query from the CLI:

```bash
sqlproctor check examples/queries/revenue_by_region_bad.sql
```

## The before/after

The demo seeds a real retail warehouse and asks one ordinary question: revenue by region. The naive query joins `shipments` and silently inflates revenue from the true **$3,550,201** to **$8,767,834**, a **2.47x** overstatement. sqlproctor blocks it, explains the fan-out, the agent drops the join, and the corrected query returns the true number with a `verified against contract v1` badge. All figures come from the seeded database, not from hand-written asserts.

The accuracy benchmark (`python demo/eval.py`) runs a mix of correct and wrong queries with known outcomes. On the current set of 13:

![Agent accuracy rises from 31% without sqlproctor to 85% with it, with zero false positives](https://cdn.jsdelivr.net/gh/debabsah/sqlproctor@main/docs/img/accuracy-uplift.svg)

| Metric | Result |
|--------|--------|
| Wrong queries caught | 7 of 9 (78%) |
| False positives on correct queries | 0 |
| Agent accuracy without sqlproctor | 31% |
| Agent accuracy with sqlproctor | 85% |
| Uplift | +54 points |

Two of the nine wrong queries are deliberately beyond what a schema-level contract can see (a status typo and a metric mislabelled under a non-metric alias), so the 78% is an honest denominator, not a rigged 100%. The +54-point uplift is the whole thesis in one number: verification converts directly into accuracy.

## Proof: this happens to frontier models, not just hand-written queries

The benchmark above answers one question (does the guard convert to accuracy: yes, 31% to 85%). `demo/live_eval.py` answers the harder one: do **real** models, given only the `CREATE TABLE` schema and never the contract's rules, produce these errors on their own? We ran five models (Claude Opus 4.8, Gemini 3.1 Pro, GPT-5.5, GLM-5.2, and a local Qwen 3.6) across three schemas: the retail and SaaS demos plus the industry-standard **TPC-DS** benchmark. Every figure below is provenance-stamped in [`results/RESULTS.md`](results/RESULTS.md) and reproducible.

**Every model trips the contract on its own first query.** On the retail schema (16 questions, reasoning effort high):

![First query blocked per model on retail: Claude Opus 4.8 15/16, Gemini 3.1 Pro 9/16, GPT-5.5 8/16, GLM-5.2 8/16](https://cdn.jsdelivr.net/gh/debabsah/sqlproctor@main/docs/img/blocked-per-model.svg)

| Model | First query blocked | Self-corrected | Reached a verified answer |
| --- | --- | --- | --- |
| Claude Opus 4.8 | 15 / 16 | 15 / 15 | 16 / 16 |
| Gemini 3.1 Pro | 9 / 16 | 9 / 9 | 16 / 16 |
| GPT-5.5 | 8 / 16 | 8 / 8 | 16 / 16 |
| GLM-5.2 | 8 / 16 | 6 / 8 | 14 / 16 |

This is **not a capability ranking.** Opus blocks highest because it writes direct SQL without defensively guessing the hidden soft-delete rule; no model can infer these business rules from DDL alone. Each one self-corrects once the guard hands back the specific violation.

**The credibility centerpiece.** Asked "total quantity of items sold by shipping carrier" (true answer 40,756), Claude Opus 4.8 and Gemini 3.1 Pro *independently* wrote the identical CTE-laundered fan-out and both returned **101,027** (a 2.48x overstatement). V1 missed it; these runs exposed the blind spot; it is now **closed** ([`docs/LIMITATIONS.md`](docs/LIMITATIONS.md) #1). GPT-5.5 gave a defensible 47,138; GLM-5.2 held the line and emitted no number rather than a wrong one. Finding our own hole across two frontier models and closing it is the loop working on the tool itself.

![Claude Opus 4.8 and Gemini 3.1 Pro both returned the identical 101,027, 2.48x the true answer of 40,756; GPT-5.5 a defensible 47,138; GLM-5.2 held the line and emitted no number](https://cdn.jsdelivr.net/gh/debabsah/sqlproctor@main/docs/img/truth-line.svg)

**On a recognized benchmark.** On **TPC-DS**, all three frontier models wrote the store-sales fan-out from the schema alone: true store sales are **$4.74B**, and a naive join through a shared dimension inflates that to **$95.4B**, a **20x** silent overstatement. The guard blocked it and every model self-corrected.

Run it yourself (no rules leaked into the prompt; the model self-corrects on the structured feedback):

```bash
pip install -e ".[demo-live]"                        # anthropic + openai SDKs
export OPENROUTER_API_KEY=...                         # or ANTHROPIC_API_KEY for Claude direct
export SQLPROCTOR_LLM_PROVIDER=openrouter SQLPROCTOR_LLM_MODEL=z-ai/glm-5.2
python demo/live_eval.py --schema tpcds              # or: retail | saas
```

Offline self-test (no key): `python demo/live_eval.py --selftest`. Every run stamps provenance (timestamp, git commit, model, contract hash) to `results/` and writes a full per-turn transcript to `results/transcripts/`.

### The honest boundary

- **"Verified" means contract-clean, not ground-truth-correct.** A query the contract does not model can still be wrong, which is exactly why the carrier case above matters.
- **Blocked-rate is not model quality, and the dollar magnitudes are properties of the seeded data**, not facts about your warehouse. What transfers is the mechanism (a deterministic catch of silent, multiplicative errors) and the ground-truth rate (31% to 85%).

## The semantic contract

A contract is plain YAML, versioned in git. It declares the tables, keys, legal join edges, mandatory predicates, and metric grains that a correct query must respect. See `contracts/retail.yml`.

You do not hand-write it from scratch. `sqlproctor bootstrap` generates the tedious surface (tables, columns, keys, join graph) from a live database, and you curate the delta that schema cannot express: the mandatory filters and the metric definitions.

## How it plugs in

sqlproctor ships as a Model Context Protocol (MCP) server exposing one verified `query` tool over an embedded DuckDB. Point any MCP client (for example Claude) at it, and every query the agent runs is verified first: blocked queries return structured violations for self-correction, passing queries return rows tagged with the contract version. The same core runs as a CLI check for SQL in pull requests, and as an importable library inside any text-to-SQL product.

### Pass-through mode

sqlproctor can run as a verifying proxy in front of an existing database MCP server. The agent talks only to sqlproctor; verified queries are forwarded to the upstream, blocked ones never reach it. No change to the agent or the upstream, config only:

```bash
export SQLPROCTOR_UPSTREAM_CMD="python -m your_database_mcp"   # the upstream stdio server
export SQLPROCTOR_UPSTREAM_TOOL=query                          # its SQL tool (default: query)
export SQLPROCTOR_UPSTREAM_SQL_ARG=sql                         # that tool's SQL arg (default: sql)
export SQLPROCTOR_CONTRACT=contracts/your_warehouse.yml
export SQLPROCTOR_DIALECT=tsql                                 # match the upstream (e.g. SQL Server)
sqlproctor serve
```

See it locally against a stand-in upstream server:

```bash
python demo/passthrough_demo.py
```

Two honest gaps to close before this is production dogfooding against a real SQL Server estate:

- **Contract bootstrap is DuckDB-only.** `sqlproctor bootstrap` reads `duckdb_constraints()`; a SQL Server bootstrap (over `information_schema` plus the SQL Server key views) is not written yet, so the contract is hand-authored or partially bootstrapped for now.
- **The `tsql` verify path is not yet exercised against a real SQL Server contract.** The checks are dialect-general (sqlglot parses T-SQL), but this needs a real-schema test.
- The upstream connects per query; hold a persistent session if latency matters.

## Status

V1, validated. The five checks are ported from a proven feasibility spike and exercised against three schemas and five models (see the proof above). One blind spot the runs surfaced, a CTE-laundered fan-out that fooled two frontier models, has been found and closed; the remaining known limitations are documented honestly in [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md). This is a validation milestone, not a finished product: a SQL Server bootstrap and a real-warehouse dogfood are still open (see pass-through mode above).

## License

MIT. See [LICENSE](LICENSE).
