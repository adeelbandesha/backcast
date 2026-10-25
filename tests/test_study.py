import json

import numpy as np
import pandas as pd
import pytest

from backcast.evaluate import run_backtest
from backcast.models import Drift, Naive
from backcast.study import placebo, placebo_signs, rows


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
