import importlib.util
import pathlib

import pytest

from sqlproctor.contract import Contract

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def seeded_db():
    """Ensure the demo DuckDB exists (build it if missing) for DB-backed tests."""
    db = ROOT / "examples" / "retail.duckdb"
    if not db.exists():
        spec = importlib.util.spec_from_file_location("seed", ROOT / "examples" / "seed.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.build()
    return str(db)


@pytest.fixture
def contract() -> Contract:
    """The canonical retail contract, loaded from the shipped YAML.

    Every verifier test runs against this file, so the tests also prove the YAML
    encodes the same truth the validated spike used as a Python dict.
    """
    return Contract.from_yaml(str(ROOT / "contracts" / "retail.yml"))
