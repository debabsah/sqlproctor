"""Lock the benchmark so it cannot quietly rot: no false positives, a real catch
rate, every wrong case materially wrong, and a genuine accuracy uplift.
"""

import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_benchmark_metrics(seeded_db):
    m = _load("pl_eval", "demo/eval.py").run_eval()
    assert m["false_positives"] == 0
    assert m["catch_rate"] >= 0.7               # honest denominator includes 2 uncatchable
    assert m["all_material"]                     # every wrong query really returns a wrong answer
    assert m["kind_matches"]                     # each catchable case raised its expected kind
    assert m["with_accuracy"] > m["without_accuracy"]


def test_seed_fan_out_is_real_and_dramatic(seeded_db):
    seed = _load("seed", "examples/seed.py")
    true_rev, naive_rev, ratio = seed.revenue_numbers(seeded_db)
    assert true_rev > 3_000_000                  # a real amount of money
    assert 2.3 < ratio < 2.7                     # the inflation is ~2.5x, not contrived
    assert naive_rev > true_rev
