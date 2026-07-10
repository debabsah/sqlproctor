import pathlib

from sqlproctor.cli import main

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONTRACT = str(ROOT / "contracts" / "retail.yml")
QUERIES = ROOT / "examples" / "queries"


def test_check_bad_query_exits_nonzero(capsys):
    rc = main(["check", str(QUERIES / "revenue_by_region_bad.sql"), "--contract", CONTRACT])
    assert rc == 1
    err = capsys.readouterr().err
    assert "FAN_OUT" in err


def test_check_good_query_exits_zero(capsys):
    rc = main(["check", str(QUERIES / "revenue_by_region_good.sql"), "--contract", CONTRACT])
    assert rc == 0
    out = capsys.readouterr().out
    assert "verified against contract v1" in out
