"""Live-LLM proof: drive a real agent through the sqlproctor proxy.

The point is to answer the objection the hand-written benchmark can't: "you wrote
the wrong queries yourself." Here a real model writes the SQL. It gets the raw
schema (the CREATE TABLE statements) but NOT the contract's rules: nothing about
the soft-delete convention, the revenue grain, or which joins fan out. The
questions are ordinary business questions, never "write a wrong query." We measure
how often the model's own first query violates the contract, and whether it
self-corrects on sqlproctor's structured feedback. If the model gets everything
right first try, that is a real finding, reported as such.

Works with any tool-calling model, via three backends:

  Anthropic (default):
    export ANTHROPIC_API_KEY=...
    python demo/live_eval.py

  OpenRouter (OpenAI-compatible; GLM, and anything else it hosts):
    export OPENROUTER_API_KEY=...
    export SQLPROCTOR_LLM_PROVIDER=openrouter
    export SQLPROCTOR_LLM_MODEL=z-ai/glm-5.2     # default for this provider
    python demo/live_eval.py

  Local (any OpenAI-compatible server, e.g. llama.cpp / vLLM / Ollama):
    export SQLPROCTOR_LLM_PROVIDER=local
    export SQLPROCTOR_LLM_BASE_URL=http://HOST:PORT/v1   # no API key required
    export SQLPROCTOR_LLM_MODEL=<the model id the server serves>
    python demo/live_eval.py

Two demo warehouses: --schema retail (default) or --schema saas (or SQLPROCTOR_SCHEMA).
Set SQLPROCTOR_LLM_TEMPERATURE=0 to pin a reproducible run. Set SQLPROCTOR_LLM_EFFORT
(low|medium|high|xhigh|max) to control reasoning depth; it is recorded per row.
For anthropic it drives output_config.effort + adaptive thinking (do NOT also set
temperature, which current Claude models reject); for openrouter it drives the
reasoning.effort field. Claude runs go direct to the Anthropic API (unset
ANTHROPIC_BASE_URL so the SDK doesn't hit a local server).
Offline plumbing check (no key, no network):  python demo/live_eval.py --selftest
Each run's result is saved to results/ (see results/RESULTS.md).
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

# ---------------------------------------------------------- question banks ----
# Ordinary business questions. The model gets the raw schema but never the
# contract's rules, and is never told to "write a wrong query." Each bank spans
# all five checks plus a few questions the schema genuinely under-determines
# (where the honest outcome is sqlproctor holding the line, not a number).
RETAIL_QUESTIONS = [
    "What was our total revenue?",
    "What is our revenue broken down by customer region?",
    "How much revenue came from orders shipped by FedEx?",
    "What is the total quantity of items sold, grouped by shipping carrier?",
    "Which three regions generated the most revenue?",
    "How many orders have we received?",
    "What is our average revenue per order?",
    "What is our total revenue from completed orders?",
    "How many items were shipped via UPS?",
    "Which shipping carrier handled the most orders?",
    "What is our revenue per region, for completed orders only?",
    "How many distinct customers have placed an order?",
    "What is our revenue per shipping carrier?",
    "How much revenue has each product generated?",
    "What is our total revenue, counting only orders that were actually shipped?",
    "What is the average number of shipments per order?",
]
SAAS_QUESTIONS = [
    "What is our total billed revenue?",
    "What is our billed revenue broken down by account region?",
    "How much have we collected in payments so far?",
    "What is our billed revenue by payment method?",
    "What is our current monthly recurring revenue (MRR)?",
    "What is our MRR broken down by plan tier?",
    "How many active subscriptions do we have?",
    "What is the total usage, in units, per subscription?",
    "What is our billed revenue from enterprise accounts?",
    "How much revenue did we bill in each region, counting only invoices that were paid?",
    "What is the average invoice amount?",
    "What is our total billed revenue per plan tier?",
    "How many distinct accounts have been billed?",
    "What is our average billed revenue per account?",
]
TPCDS_QUESTIONS = [
    "What were our total store sales?",
    "What were our total store sales by item category?",
    "What are our net store sales after returns, broken down by item?",
    "How much did customers return, in dollars, by store?",
    "What were our total store sales and total returns for each item category?",
    "Which items were returned the most, compared with how many were sold?",
    "What were our store sales in the year 2001?",
    "What is the average sales price per item?",
    "What is our net store revenue (sales minus returns) per customer?",
    "How many separate item returns were recorded?",
    "What were our total store sales by state?",
    "What is our total store sales revenue including tax?",
    "What is the total quantity of items sold and returned, per store?",
    "Which state had the highest return rate?",
]

def _bird_financial_questions() -> list:
    """The BIRD financial question bank (question + evidence), loaded from the fetched
    mini-dev JSON. Empty if the data has not been downloaded yet."""
    try:
        import importlib.util
        p = ROOT / "examples" / "bird_financial_seed.py"
        spec = importlib.util.spec_from_file_location("bird_financial_seed", p)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m.questions()
    except Exception:
        return []


# ------------------------------------------------------------ schema select ----
# Which warehouse this run targets. sqlproctor's embedded query path binds the contract/db
# (and dialect) at import, so the schema must be chosen before importing mcp_server.
SCHEMAS = {
    "retail": {"seed": "seed.py", "contract": ROOT / "contracts" / "retail.yml",
               "db": ROOT / "examples" / "retail.duckdb", "questions": RETAIL_QUESTIONS},
    "saas": {"seed": "saas_seed.py", "contract": ROOT / "contracts" / "saas.yml",
             "db": ROOT / "examples" / "saas.duckdb", "questions": SAAS_QUESTIONS},
    "tpcds": {"seed": "tpcds_seed.py", "contract": ROOT / "contracts" / "tpcds.yml",
              "db": ROOT / "examples" / "tpcds.duckdb", "questions": TPCDS_QUESTIONS},
    "bird_financial": {"seed": "bird_financial_seed.py",
                       "contract": ROOT / "contracts" / "bird_financial.yml",
                       "db": ROOT / "examples" / "bird" / "financial.sqlite",
                       "dialect": "sqlite",
                       "questions": _bird_financial_questions()},
}


def _resolve_schema(argv) -> str:
    val = os.environ.get("SQLPROCTOR_SCHEMA")
    if "--schema" in argv and argv.index("--schema") + 1 < len(argv):
        val = argv[argv.index("--schema") + 1]
    val = (val or "retail").lower()
    if val not in SCHEMAS:
        raise SystemExit(f"unknown --schema {val!r} (choose: {', '.join(SCHEMAS)})")
    return val


SCHEMA = _resolve_schema(sys.argv[1:])
_CFG = SCHEMAS[SCHEMA]
os.environ["SQLPROCTOR_CONTRACT"] = str(_CFG["contract"])
os.environ["SQLPROCTOR_DB"] = str(_CFG["db"])
if _CFG.get("dialect"):
    os.environ["SQLPROCTOR_DIALECT"] = _CFG["dialect"]
os.environ.setdefault("SQLPROCTOR_LEDGER", str(ROOT / "live_ledger.jsonl"))

from sqlproctor import mcp_server  # noqa: E402

PROVIDER = os.environ.get("SQLPROCTOR_LLM_PROVIDER", "anthropic").lower()
_DEFAULT_MODEL = {
    "anthropic": "claude-opus-4-8",
    "openrouter": "z-ai/glm-5.2",
    "openai": "gpt-4o",
    "local": "local-model",
}
MODEL = os.environ.get("SQLPROCTOR_LLM_MODEL", _DEFAULT_MODEL.get(PROVIDER, "claude-opus-4-8"))
_temp = os.environ.get("SQLPROCTOR_LLM_TEMPERATURE")
TEMPERATURE = float(_temp) if _temp not in (None, "") else None
# Reasoning models (and quantized local ones) can spend their budget on chain-of-
# thought and truncate the tool-call args mid-string. Bump this for such servers.
MAX_TOKENS = int(os.environ.get("SQLPROCTOR_LLM_MAX_TOKENS", "2048"))
# Reasoning effort. Anthropic: output_config.effort + adaptive thinking. OpenRouter:
# the `reasoning.effort` field. Recorded per row (see save_result below).
EFFORT = os.environ.get("SQLPROCTOR_LLM_EFFORT") or None
MAX_TURNS = 5
QUESTIONS = _CFG["questions"]

_TOOL_NAME = "query"
_TOOL_DESC = "Run a read-only SQL query against the warehouse and get the rows back."
_TOOL_PARAMS = {
    "type": "object",
    "properties": {"sql": {"type": "string", "description": "The SQL to run (DuckDB dialect)."}},
    "required": ["sql"],
}
ANTHROPIC_TOOL = {"name": _TOOL_NAME, "description": _TOOL_DESC, "input_schema": _TOOL_PARAMS}
OPENAI_TOOL = {"type": "function",
               "function": {"name": _TOOL_NAME, "description": _TOOL_DESC, "parameters": _TOOL_PARAMS}}


class MissingKey(Exception):
    def __init__(self, env):
        self.env = env


def _load_seed():
    spec = importlib.util.spec_from_file_location(
        f"seed_{SCHEMA}", ROOT / "examples" / _CFG["seed"])
    seed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed)
    return seed


def _ensure_seeded():
    db = pathlib.Path(_CFG["db"])
    if not db.exists():
        _load_seed().build(str(db))


def _system_prompt() -> str:
    # Raw DDL only. No contract rules leak into the prompt.
    return (
        "You are a data analyst answering business questions by querying a company's "
        "DuckDB warehouse. Use the `query` tool to run read-only SQL. If a query is "
        "rejected, read the reason and try again. Here is the schema:\n\n"
        + _load_seed().SCHEMA
        + "\nWhen you have the answer, state it in one sentence including the number."
    )


# --------------------------------------------------------------- backends ----
class AnthropicBackend:
    def __init__(self, model):
        import anthropic
        self.client = anthropic.Anthropic()
        self.model = model

    def start(self, system, question):
        self.system = system
        self.messages = [{"role": "user", "content": question}]

    def step(self):
        kwargs = dict(model=self.model, max_tokens=MAX_TOKENS, system=self.system,
                      tools=[ANTHROPIC_TOOL], messages=self.messages)
        if TEMPERATURE is not None:  # NB: 4.7+ models reject temperature; leave it unset
            kwargs["temperature"] = TEMPERATURE
        if EFFORT is not None:  # reasoning depth on current Claude models
            kwargs["output_config"] = {"effort": EFFORT}
            kwargs["thinking"] = {"type": "adaptive"}
        resp = self.client.messages.create(**kwargs)
        if resp.stop_reason == "tool_use":
            self.messages.append({"role": "assistant", "content": resp.content})
            calls = [(b.id, (b.input or {}).get("sql", "")) for b in resp.content
                     if b.type == "tool_use" and b.name == _TOOL_NAME]
            return calls, resp.stop_reason
        return [], resp.stop_reason

    def feed(self, results):
        self.messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": cid, "content": json.dumps(p)}
            for cid, p in results]})


class OpenAIBackend:
    """OpenAI-compatible chat/completions, incl. OpenRouter (GLM, etc.)."""

    def __init__(self, model, base_url, api_key):
        from openai import OpenAI
        self.client = OpenAI(base_url=base_url, api_key=api_key)
        self.model = model

    def start(self, system, question):
        self.messages = [{"role": "system", "content": system},
                         {"role": "user", "content": question}]

    def step(self):
        kwargs = dict(model=self.model, max_tokens=MAX_TOKENS, tools=[OPENAI_TOOL],
                      messages=self.messages)
        if TEMPERATURE is not None:
            kwargs["temperature"] = TEMPERATURE
        if EFFORT is not None and PROVIDER in ("openrouter", "openai"):
            # OpenRouter's unified reasoning control (local llama.cpp doesn't take it)
            kwargs["extra_body"] = {"reasoning": {"effort": EFFORT}}
        resp = self.client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        tool_calls = choice.message.tool_calls or []
        if tool_calls:
            self.messages.append({
                "role": "assistant",
                "content": choice.message.content,
                "tool_calls": [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in tool_calls],
            })
            calls = []
            for tc in tool_calls:
                if tc.function.name == _TOOL_NAME:
                    try:
                        sql = json.loads(tc.function.arguments).get("sql", "")
                    except (json.JSONDecodeError, TypeError):
                        sql = ""
                    calls.append((tc.id, sql))
            return calls, choice.finish_reason
        self.messages.append({"role": "assistant", "content": choice.message.content or ""})
        return [], choice.finish_reason

    def feed(self, results):
        for cid, p in results:
            self.messages.append({"role": "tool", "tool_call_id": cid, "content": json.dumps(p)})


def _key_env() -> str:
    return {"anthropic": "ANTHROPIC_API_KEY", "openrouter": "OPENROUTER_API_KEY",
            "openai": "OPENAI_API_KEY", "local": "SQLPROCTOR_LLM_API_KEY"}.get(PROVIDER, "ANTHROPIC_API_KEY")


def build_backend():
    if PROVIDER == "anthropic":
        return AnthropicBackend(MODEL)
    if PROVIDER == "local":
        # Any OpenAI-compatible local server (llama.cpp / vLLM / Ollama). No real
        # key; the base URL is the only thing we require, and it is not a secret.
        base_url = os.environ.get("SQLPROCTOR_LLM_BASE_URL")
        if not base_url:
            raise MissingKey("SQLPROCTOR_LLM_BASE_URL")
        api_key = os.environ.get("SQLPROCTOR_LLM_API_KEY", "sk-local")
        return OpenAIBackend(MODEL, base_url, api_key)
    if PROVIDER in ("openrouter", "openai"):
        default_base = "https://openrouter.ai/api/v1" if PROVIDER == "openrouter" else None
        base_url = os.environ.get("SQLPROCTOR_LLM_BASE_URL", default_base)
        api_key = os.environ.get(_key_env())
        if not api_key:
            raise MissingKey(_key_env())
        return OpenAIBackend(MODEL, base_url, api_key)
    raise ValueError(
        f"unknown SQLPROCTOR_LLM_PROVIDER: {PROVIDER!r} (anthropic | openrouter | openai | local)")


# ------------------------------------------------------------ measurement ----
def run_question(backend, question: str) -> dict:
    backend.start(_system_prompt(), question)
    trace, first_blocked, first_kinds, verified = [], None, [], False
    stop = None
    for _ in range(MAX_TURNS):
        calls, stop = backend.step()
        if not calls:
            break
        results = []
        for cid, sql in calls:
            outcome = mcp_server.run_query(sql)
            kinds = [v["kind"] for v in outcome.get("violations", [])]
            if first_blocked is None:
                first_blocked = outcome["status"] == "blocked"
                first_kinds = kinds
            if outcome["status"] == "verified":
                verified = True
            trace.append({"sql": sql.strip(), "status": outcome["status"], "kinds": kinds,
                          "error": outcome.get("error")})
            payload = dict(outcome)
            if "rows" in payload:
                payload["rows"] = payload["rows"][:50]
            results.append((cid, payload))
        backend.feed(results)
    return {
        "question": question,
        "queried": first_blocked is not None,
        "first_blocked": bool(first_blocked),
        "first_kinds": first_kinds,
        "verified": verified,
        "turns": len(trace),
        "trace": trace,
        "stop": stop,
    }


def _errored_record(question: str, e: Exception) -> dict:
    """A question the backend could not complete (e.g. server 500). Shaped like a
    normal record so summarize/report/transcript handle it, but excluded from rates."""
    return {"question": question, "queried": False, "first_blocked": False,
            "first_kinds": [], "verified": False, "turns": 0, "trace": [],
            "stop": "error", "error": f"{type(e).__name__}: {str(e)[:200]}"}


def summarize(records: list[dict]) -> dict:
    first_blocked = [r for r in records if r["first_blocked"]]
    kinds: dict[str, int] = {}
    for r in first_blocked:
        for k in r["first_kinds"]:
            kinds[k] = kinds.get(k, 0) + 1
    return {
        "n": len(records),
        "first_blocked": len(first_blocked),
        "verified": len([r for r in records if r["verified"]]),
        "self_corrected": len([r for r in records if r["first_blocked"] and r["verified"]]),
        "kinds": kinds,
        "errored": len([r for r in records if r.get("error")]),
    }


def _print_report(records, m):
    print("\n" + "=" * 72)
    print(f"Live-LLM proof  [{SCHEMA}]  ({PROVIDER}: {MODEL}, raw schema only, no contract rules given)")
    print("=" * 72)
    for r in records:
        print(f"\nQ: {r['question']}")
        if r.get("error"):
            print(f"   ERRORED (no verdict, excluded from rates): {r['error']}")
            continue
        mark = "clean first try" if not r["first_blocked"] else \
               "CAUGHT: " + ",".join(r["first_kinds"])
        fixed = " -> self-corrected to verified" if (r["first_blocked"] and r["verified"]) else \
                ("" if not r["first_blocked"] else " -> NOT fixed")
        print(f"   {mark}{fixed}  ({r['turns']} quer{'y' if r['turns'] == 1 else 'ies'})")
        for t in r["trace"]:
            tag = t["status"].upper() + (f" [{','.join(t['kinds'])}]" if t["kinds"] else "")
            print(f"     {tag}: {t['sql'][:90]}")
    print("\n" + "-" * 72)
    print(f"  questions:                       {m['n']}")
    if m.get("errored"):
        print(f"  errored (no verdict, excluded):  {m['errored']}/{m['n']}")
    print(f"  first query violated contract:   {m['first_blocked']}/{m['n']}")
    print(f"  of those, self-corrected:        {m['self_corrected']}/{m['first_blocked']}")
    print(f"  reached a verified answer:        {m['verified']}/{m['n']}")
    if m["kinds"]:
        print("  violation kinds the model produced: "
              + ", ".join(f"{k}x{v}" for k, v in sorted(m["kinds"].items())))
    if m["first_blocked"] == 0:
        print("\n  NOTE: the model wrote contract-clean SQL on every question. That is a")
        print("  real result. sqlproctor's value then lives in the tail: cheaper models,")
        print("  larger and messier schemas, and the queries a stronger model still gets wrong.")
    print("-" * 72)


def selftest() -> int:
    """Offline: verify the tool backing and the metric math without an API key."""
    _ensure_seeded()
    seed = _load_seed()  # each seed exposes a fan-out (bad) and a correct (good) revenue SQL
    assert mcp_server.run_query(seed.NAIVE_REVENUE_SQL)["status"] == "blocked"
    assert mcp_server.run_query(seed.TRUE_REVENUE_SQL)["status"] == "verified"

    fake = [
        {"first_blocked": True, "first_kinds": ["FAN_OUT"], "verified": True},
        {"first_blocked": True, "first_kinds": ["REQUIRED_FILTER"], "verified": False},
        {"first_blocked": False, "first_kinds": [], "verified": True},
    ]
    m = summarize(fake)
    assert m == {"n": 3, "first_blocked": 2, "verified": 2, "self_corrected": 1,
                 "kinds": {"FAN_OUT": 1, "REQUIRED_FILTER": 1}, "errored": 0}, m
    print(f"selftest OK [{SCHEMA}]: run_query integration + metric math verified (no API used).")
    return 0


def _is_auth_error(e) -> bool:
    if isinstance(e, TypeError):
        return "authentication" in str(e).lower()  # anthropic: no credential source
    return type(e).__name__ in ("AuthenticationError", "PermissionDeniedError")


def _is_api_error(e) -> bool:
    return any(b.__name__ == "APIError" for b in type(e).__mro__)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--selftest" in argv:
        return selftest()

    _ensure_seeded()
    if (ROOT / "live_ledger.jsonl").exists():
        (ROOT / "live_ledger.jsonl").unlink()

    try:
        backend = build_backend()
    except MissingKey as e:
        print(f"No credentials: set {e.env} then re-run.", file=sys.stderr)
        return 2

    # Per-question isolation: one flaky completion (e.g. a local server returning a
    # 500 on a malformed tool call) must not discard the other questions' results.
    records = []
    for q in QUESTIONS:
        try:
            records.append(run_question(backend, q))
        except Exception as e:  # noqa: BLE001 - classify SDK errors, re-raise real bugs
            if _is_auth_error(e):
                print(f"Authentication failed ({type(e).__name__}). Check {_key_env()}.", file=sys.stderr)
                return 2
            if _is_api_error(e):
                print(f"  API error on one question ({type(e).__name__}); recording as errored, "
                      "continuing.", file=sys.stderr)
                records.append(_errored_record(q, e))
                continue
            raise

    m = summarize(records)
    _print_report(records, m)

    from sqlproctor import results
    contract = mcp_server.get_contract()
    rec = results.save_result(ROOT / "results", "live_eval", {
        "model": MODEL,
        "provider": PROVIDER,
        "schema": SCHEMA,
        "effort": EFFORT or "",
        "contract_version": contract.version_label(),
        "contract_sha256": contract.content_hash(),
        "n": m["n"], "first_blocked": m["first_blocked"],
        "self_corrected": m["self_corrected"], "verified": m["verified"],
        "kinds": m["kinds"], "errored": m["errored"],
    }, key_fields=("git_sha", "model", "contract_sha256", "effort"))
    print("\n  saved to results/live_eval.jsonl (see results/RESULTS.md)")

    # Full per-turn transcript: every query the model wrote, and each verdict.
    tdir = ROOT / "results" / "transcripts"
    tdir.mkdir(parents=True, exist_ok=True)
    stamp = rec["ts"].replace(":", "").replace("-", "")
    _eff = f"_{EFFORT}" if EFFORT else ""
    tpath = tdir / f"{SCHEMA}_{PROVIDER}_{MODEL.replace('/', '_')}{_eff}_{stamp}.json"
    tpath.write_text(json.dumps({
        "meta": {"provider": PROVIDER, "model": MODEL, "schema": SCHEMA, "effort": EFFORT,
                 "ts": rec["ts"], "git_sha": rec["git_sha"],
                 "contract_version": contract.version_label(),
                 "contract_sha256": contract.content_hash()},
        "summary": m, "records": records,
    }, indent=2))
    print(f"  transcript saved to {tpath.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
