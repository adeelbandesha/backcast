import json

import numpy as np
import pandas as pd
import pytest

from backcast.evaluate import run_backtest
from backcast.models import Drift, Naive
from backcast.study import findings, placebo, placebo_signs, rows


def prices(seed, periods=1250, name="ABC") -> pd.Series:
    values = 100 * np.exp(np.cumsum(np.random.default_rng(seed).normal(0.001, 0.01, periods)))
    series = pd.Series(values, index=pd.bdate_range(end="2026-10-23", periods=periods), name=name)
    series.attrs = {"calendar": "XNYS"}
    return series


def test_placebo_keeps_every_move_and_changes_only_its_direction():
    real = prices(0)
    signs = placebo_signs("2020-01-01", "2026-10-23")
    replayed = placebo(real, signs)
    assert replayed.index.equals(real.index) and replayed.iloc[0] == real.iloc[0]
    assert (replayed.name, replayed.attrs) == ("ABC", {"calendar": "XNYS"})
    moves, replayed_moves = np.log(real).diff().iloc[1:], np.log(replayed).diff().iloc[1:]
    assert np.allclose(replayed_moves.abs(), moves.abs())
    assert np.array_equal(np.sign(replayed_moves), np.sign(moves) * signs.reindex(moves.index))


def test_placebo_signs_are_shared_and_reproducible():
    signs = placebo_signs("2020-01-01", "2026-10-23")
    assert signs.equals(placebo_signs("2020-01-01", "2026-10-23"))
    assert set(signs.unique()) == {-1.0, 1.0} and abs(signs.mean()) < 0.05
    one, other = placebo(prices(1), signs), placebo(prices(2, name="XYZ"), signs)
    flips = lambda p, q: np.sign(np.log(p).diff()) != np.sign(np.log(q).diff())  # noqa: E731
    assert flips(prices(1), one).equals(flips(prices(2), other))


def test_placebo_needs_a_sign_for_every_day():
    with pytest.raises(ValueError, match="No placebo sign"):
        placebo(prices(0), placebo_signs("2025-01-01", "2026-10-23"))


def test_rows_are_one_per_model_and_plain_json():
    report = run_backtest(prices(3), 5, [Naive(), Drift()])
    lines = rows(report, "placebo", 7)
    assert sorted(line["model"] for line in lines) == ["drift", "naive"]
    assert {line["symbol"] for line in lines} == {"ABC"} and {line["horizon"] for line in lines} == {5}
    assert {(line["kind"], line["seed"]) for line in lines} == {("placebo", 7)}
    assert lines[0]["origins"] == 504 and lines[0]["last_target"] == "2026-10-23"
    assert lines[0]["one_way"] == report.verdict.one_way
    by_model = {line["model"]: line for line in lines}
    assert by_model["naive"]["relative_mae_up"] == 1.0
    assert by_model["drift"]["relative_mae_up"] == report.trend.loc[True, "drift"]
    json.dumps(lines, allow_nan=False)


def study_row(symbol, model, relative_mae, p_adjusted, best="drift", spa=0.5, seed=None, one_way=None):
    return dict(symbol=symbol, name=f"{symbol} Inc.", horizon=5, kind="real" if seed is None else "placebo",
                seed=seed, model=model, best=best, relative_mae=relative_mae, relative_mae_up=relative_mae,
                relative_mae_down=1.0, p_adjusted=p_adjusted, spa_p_value=spa, significant=spa < 0.05,
                period_change=0.1, one_way=one_way)  # fmt: skip


def test_findings_compares_real_stocks_with_every_placebo_draw():
    results = pd.DataFrame(
        [
            study_row("A", "drift", 0.95, 0.01, spa=0.02, one_way="rise"),
            study_row("A", "ets", 0.99, 0.30, spa=0.02, one_way="rise"),
            study_row("B", "ets", 0.98, 0.04, best="ets", spa=0.03),
            study_row("B", "drift", 1.01, 0.90, best="ets", spa=0.03),
            study_row("C", "drift", 1.02, 1.0, spa=0.8, one_way="fall"),
            study_row("C", "ets", 1.03, 1.0, spa=0.8, one_way="fall"),
            # Draw 1 finds a lucky winner in A; draw 2 finds none.
            *(study_row(s, m, 0.97, 0.01, spa=0.01, seed=1) for s in "A" for m in ("drift", "ets")),
            *(study_row(s, m, 1.01, 0.7, spa=0.6, seed=1) for s in "BC" for m in ("drift", "ets")),
            *(study_row(s, m, 1.01, 0.7, spa=0.6, seed=2) for s in "ABC" for m in ("drift", "ets")),
        ]
    )
    result = findings(results)
    real, fake = result["summary"][0]["real"], result["summary"][0]["placebo"]
    assert result["horizons"] == [5]
    assert (real["tested"], real["significant"], real["trend_bets"]) == (3, 2, 1)
    assert real["share"] == pytest.approx(2 / 3)
    assert (fake["draws"], fake["tested"], fake["low"]) == (2, 3, 0.0)
    assert fake["share"] == pytest.approx(1 / 6) and fake["high"] == pytest.approx(1 / 3)
    assert result["models"]["drift"] == [{"real": pytest.approx(1 / 3), "placebo": pytest.approx(1 / 6)}]
    assert sorted(result["p_values"][5]["real"]) == [0.02, 0.03, 0.8]
    assert sorted(result["p_values"][5]["placebo"]) == [0.01, 0.6, 0.6, 0.6, 0.6, 0.6]
    assert [(w["symbol"], w["best"], w["relative_mae"], w["trend_bet"]) for w in result["winners"]] == [
        ("A", "drift", 0.95, True),
        ("B", "ets", 0.98, False),
    ]
    json.dumps(result, allow_nan=False)
