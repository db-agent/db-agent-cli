from decimal import Decimal

import pytest

from db_agent.engines.sqlserver import validate as V


def fold(rows, cols=("id", "amt")):
    return V.fold_chunks([(list(cols), rows)], 10_000)


def test_fold_counts_rows_and_totals_but_keeps_no_values():
    f = fold([(1, Decimal("10.10")), (2, Decimal("5.20")), (3, None)])
    assert f.rows == 3 and f.sums["amt"] == Decimal("15.30") and f.sums["id"] == 6 and f.nonnull["amt"] == 2
    assert isinstance(f.rows, int) and all(isinstance(c, str) for c in f.columns)   # names and counts only, no row values
    assert set(vars(f)) == {'columns', 'rows', 'sums', 'nonnull', 'ms', 'truncated'}


def test_compare_pass_and_each_kind_of_failure():
    a = fold([(1, Decimal("1.00")), (2, Decimal("2.00"))])
    assert V.compare(a, fold([(1, Decimal("1.00")), (2, Decimal("2.00"))]))[0] == "pass"
    assert "row count" in V.compare(a, fold([(1, Decimal("1.00"))]))[1][0]
    assert any("amt total" in d for d in V.compare(a, fold([(1, Decimal("1.00")), (2, Decimal("2.01"))]))[1])
    assert any("columns differ" in d for d in V.compare(a, fold([(1, 2)], cols=("id", "other")))[1])
    assert any("non-null" in d for d in V.compare(a, fold([(1, Decimal("1.00")), (2, None)]))[1])


def test_float_totals_use_a_tolerance():
    a, b = fold([(1, 0.1), (2, 0.2)]), fold([(1, 0.2), (2, 0.1)])
    assert V.compare(a, b)[0] == "pass"


def test_row_cap_truncates():
    f = V.fold_chunks([(["a"], [(i,) for i in range(50)])], max_rows=10)
    assert f.truncated


REPORT = {"parameters": [{"name": "Start", "type": "DateTime", "default": "2012-01-01", "multi_value": False},
                         {"name": "Top", "type": "Integer", "default": "5", "multi_value": False},
                         {"name": "Many", "type": "Integer", "default": "1", "multi_value": True},
                         {"name": "Expr", "type": "DateTime", "default": "=Today()", "multi_value": False}]}


def ds(sql, **params):
    return {"command_text": sql, "query_parameters": [{"name": f"@{k}", "value": f"=Parameters!{v}.Value"} for k, v in params.items()]}


def test_bind_params_replaces_only_real_parameters_and_escapes_percent():
    out = V.bind_params(REPORT, ds("SELECT '@Start' AS s, name FROM t WHERE d >= @Start AND n LIKE '%a%' AND k = @Top", Start="Start", Top="Top"), {})
    sql, params = out
    assert "'@Start'" in sql and "d >= %(Start)s" in sql and "'%%a%%'" in sql and "k = %(Top)s" in sql
    assert params["Top"] == 5 and params["Start"].year == 2012


def test_bind_params_skips_with_a_reason():
    assert "multi-value" in V.bind_params(REPORT, ds("SELECT 1 WHERE x IN (@Many)", Many="Many"), {})
    assert "params.yaml" in V.bind_params(REPORT, ds("SELECT 1 WHERE d = @Expr", Expr="Expr"), {})
    assert "params.yaml" in V.bind_params(REPORT, ds("SELECT 1 WHERE d = @Missing"), {})


def test_overrides_win_over_defaults():
    _, params = V.bind_params(REPORT, ds("SELECT 1 WHERE d = @Expr", Expr="Expr"), {"Expr": "2020-02-02"})
    assert params["Expr"].year == 2020


def test_no_parameters_returns_sql_unchanged():
    assert V.bind_params(REPORT, ds("SELECT 100 AS pct_x"), {}) == ("SELECT 100 AS pct_x", {})
