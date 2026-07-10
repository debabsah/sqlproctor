import pathlib

import duckdb

from sqlproctor.bootstrap import bootstrap_to_contract, bootstrap_from_path
from sqlproctor.contract import Contract

ROOT = pathlib.Path(__file__).resolve().parents[1]
DB = str(ROOT / "examples" / "retail.duckdb")
RETAIL = str(ROOT / "contracts" / "retail.yml")


def _generated():
    con = duckdb.connect(DB, read_only=True)
    try:
        return bootstrap_to_contract(con)
    finally:
        con.close()


def test_bootstrap_reproduces_tables_columns_and_pks(seeded_db):
    generated = _generated()
    curated = Contract.from_yaml(RETAIL)
    assert set(generated.tables) == set(curated.tables)
    for t in curated.tables:
        assert generated.tables[t].columns == curated.tables[t].columns, t
        assert generated.tables[t].pk == curated.tables[t].pk, t


def test_bootstrap_reproduces_join_graph(seeded_db):
    # edges are order-independent, so FK direction does not matter
    assert _generated().declared_edges == Contract.from_yaml(RETAIL).declared_edges


def test_bootstrap_leaves_curated_delta_empty(seeded_db):
    generated = _generated()
    assert generated.metrics == {}
    assert all(not t.required_filters for t in generated.tables.values())


def test_bootstrap_yaml_is_parseable_and_has_stubs(seeded_db):
    text = bootstrap_from_path(DB)
    assert "# required_filters:" in text  # curated stubs present for humans
    assert "# metrics:" in text
    c = Contract.from_yaml(text)
    assert set(c.tables) == {"orders", "order_items", "customers", "shipments"}
