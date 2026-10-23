import numpy as np
import pandas as pd
import pytest
from scipy.stats import ttest_1samp

from backcast.evaluate import (
    Verdict,
    _crps,
    _diebold_mariano,
    _holm,
    _interval_score,
    by_trend,
    headline,
    leaderboard,
    superior_predictive_ability,
    trend_note,
    verdict,
    walk_forward,
)
from backcast.models import QUANTILES, Drift, Forecast, Model, Naive


def series(values) -> pd.Series:
    return pd.Series(values, index=pd.bdate_range("2020-01-01", periods=len(values)), name="TEST")


def random_walk(n=700, seed=0) -> pd.Series:
    return series(100 * np.exp(np.cumsum(np.random.default_rng(seed).normal(0, 0.01, n))))


def origin_rows(model, last, actual, predicted, spread=1.0, crps=1.0, interval=1.0, uptrend=True) -> pd.DataFrame:
    """Walk-forward results written out by hand, one row per origin. Errors are on log prices, as in walk_forward."""
    origins = pd.bdate_range("2024-01-01", periods=len(last))
    return pd.DataFrame(
        {
            "model": model,
            "origin": origins,
            "target": origins + pd.Timedelta(days=7),
            "last": last,
            "actual": actual,
            "predicted": predicted,
            "lower": np.subtract(predicted, spread),
            "upper": np.add(predicted, spread),
            "error": np.abs(np.log(np.divide(actual, predicted))),
            "crps": crps,
            "interval": interval,
            "uptrend": uptrend,
        }
    )


def constant(value, h) -> Forecast:
    return Forecast(np.full(h, value), np.full((len(QUANTILES), h), value))


class CountingModel(Model):
    key, name, family, summary = "spy", "Spy", "Test", "Forecasts the number of prices it was shown."

    def forecast(self, y, h):
        return constant(float(len(y)), h)


class FitCountingModel(Model):
    key, name, family, summary = "fit-spy", "Fit spy", "Test", "Forecasts the number of prices it was fitted on."

    def fit(self, y, h):
        return lambda latest: constant(float(len(y)), h)


def test_naive_forecasts_the_last_known_price():
    prices = random_walk()
    results = walk_forward(prices, 5, [Naive()], origins=40)
    assert len(results) == 40
    assert np.allclose(results.predicted, results["last"])
    assert (results.actual.to_numpy() == prices.loc[results.target].to_numpy()).all()
    assert np.allclose(results.error, np.abs(np.log(results.actual / results["last"])))


@pytest.mark.parametrize("horizon", [1, 5, 20])
def test_models_never_see_the_price_they_are_scored_on(horizon):
    prices = random_walk()
    results = walk_forward(prices, horizon, [CountingModel()], origins=60, refit=7)
    origin = prices.index.get_indexer(results.origin)
    target = prices.index.get_indexer(results.target)
    assert (results.predicted == origin + 1).all()  # saw prices up to and including the origin
    assert (target == origin + horizon).all()
    assert (np.diff(origin) == 1).all()  # one origin per trading day
    assert target[-1] == len(prices) - 1  # the last origin is scored on the latest close


def test_models_are_refit_on_schedule_and_never_on_later_prices():
    prices = random_walk()
    results = walk_forward(prices, 5, [FitCountingModel()], origins=60, refit=7)
    origin = prices.index.get_indexer(results.origin) + 1  # number of prices known at each origin
    fitted_on = results.predicted.to_numpy()
    assert (fitted_on <= origin).all()
    assert (origin - fitted_on < 7).all()
    assert len(np.unique(fitted_on)) == 9  # 60 origins in blocks of 7


def test_crps_and_interval_score():
    # A forecast with every quantile at one value is a point forecast, and its CRPS is the absolute error.
    assert _crps(np.full(len(QUANTILES), 2.0), 3.5) == pytest.approx(1.5)
    assert _crps(QUANTILES.copy(), 0.5) < _crps(QUANTILES + 1, 0.5)
    # An 80% interval: alpha is 0.2, so a miss costs 2 / 0.2 = 10 times the distance outside, plus the width.
    assert _interval_score(1.0, 2.0, 1.5) == pytest.approx(1.0)
    assert _interval_score(1.0, 2.0, 2.5) == pytest.approx(1.0 + 10 * 0.5)
    assert _interval_score(1.0, 2.0, 0.0) == pytest.approx(1.0 + 10 * 1.0)


def test_diebold_mariano():
    improvement = np.random.default_rng(2).normal(0.1, 1, 200)
    # One day ahead there is no overlap to correct for, and the test is the paired t-test.
    assert _diebold_mariano(improvement, 1) == pytest.approx(ttest_1samp(improvement, 0).pvalue)
    assert np.isnan(_diebold_mariano(np.zeros(50), 5))


def test_diebold_mariano_holds_its_size_on_overlapping_forecasts():
    # 20-day forecasts from consecutive days: each difference is a moving sum of 20 daily shocks, with no real edge.
    rng = np.random.default_rng(0)
    samples = [np.convolve(rng.normal(0, 1, 523), np.ones(20), "valid") for _ in range(500)]
    t_test = np.mean([ttest_1samp(sample, 0).pvalue < 0.05 for sample in samples])
    dm = np.mean([_diebold_mariano(sample, 20) < 0.05 for sample in samples])
    assert t_test > 0.5  # ignoring the overlap finds an edge most of the time
    assert dm < 0.09


def test_spa_ignores_a_model_identical_to_naive():
    rng = np.random.default_rng(4)
    last = np.full(300, 100.0)
    actual = last * np.exp(rng.normal(0, 0.02, 300))
    noise = rng.normal(0, 0.01, 300)
    results = pd.concat(
        [
            origin_rows("naive", last, actual, last),
            origin_rows("arima", last, actual, last),  # chose a random walk: naive exactly
            origin_rows("drift", last, actual, last * np.exp(noise)),
        ]
    )
    p_value = superior_predictive_ability(results, 5)
    assert p_value > 0.2  # arch alone reports 0 when a model matches naive on every day
    assert np.isnan(superior_predictive_ability(results[results.model != "drift"], 5))


def test_too_little_history_is_an_error():
    with pytest.raises(ValueError, match="Need at least"):
        walk_forward(random_walk(400), 5, [Naive()])


def test_drift_wins_on_a_trend_and_the_headline_says_why():
    noise = np.random.default_rng(1).normal(0, 0.3, 700)
    results = walk_forward(series(100 + 0.5 * np.arange(700) + noise), 5, [Naive(), Drift()], origins=100)
    result = verdict(leaderboard(results, 5), results, 5)
    assert result.best == "drift" and result.beaten and result.significant and result.p_value < 0.05
    assert result.one_way == "rise" and result.one_way_origins == 100
    assert trend_note(result).startswith("It predicted a rise on all 100 days while the price rose")


def test_leaderboard_metrics():
    last = [100.0] * 4
    actual = [102.0, 98.0, 101.0, 105.0]
    results = pd.concat(
        [
            origin_rows(
                "naive", last, actual, last, spread=1.0, crps=2.0, interval=4.0, uptrend=[True] * 2 + [False] * 2
            ),
            origin_rows(
                "drift",
                last,
                actual,
                [101.0, 99.0, 103.0, 104.0],
                spread=1.5,
                crps=1.0,
                interval=5.0,
                uptrend=[True] * 2 + [False] * 2,
            ),
        ]
    )
    board = leaderboard(results, 1).set_index("model")
    error = {
        "naive": np.abs(np.log(np.divide(actual, last))),
        "drift": np.abs(np.log(np.divide(actual, [101.0, 99.0, 103.0, 104.0]))),
    }

    assert list(board.index) == ["drift", "naive"]
    assert board.loc["naive", "mae"] == pytest.approx(error["naive"].mean())
    assert board.loc["drift", "relative_mae"] == pytest.approx(error["drift"].mean() / error["naive"].mean())
    assert board.loc["drift", "relative_crps"] == pytest.approx(0.5)
    assert board.loc["drift", "relative_interval"] == pytest.approx(1.25)
    assert board.loc["drift", "win_rate"] == pytest.approx(0.75)
    assert board.loc["drift", "p_value"] == pytest.approx(ttest_1samp(error["naive"] - error["drift"], 0).pvalue)
    assert board.loc["drift", "p_adjusted"] == board.loc["drift", "p_value"]  # one model tested, nothing to adjust
    assert board.loc["drift", "direction"] == pytest.approx(1.0)
    assert np.isnan(board.loc["naive", "direction"])  # "no change" has no direction
    assert board.loc["drift", "coverage"] == pytest.approx(0.75)
    assert board.loc["naive", "coverage"] == pytest.approx(0.25)

    trend = by_trend(results)
    assert trend.loc[True, "drift"] == pytest.approx(error["drift"][:2].mean() / error["naive"][:2].mean())
    assert trend.loc[False, "drift"] == pytest.approx(error["drift"][2:].mean() / error["naive"][2:].mean())

    result = verdict(board.reset_index(), results, 1)
    assert result.beaten
    assert (result.origins, result.up_origins, result.period_change) == (4, 3, pytest.approx(0.05))
    assert result.one_way is None  # 3 rises and 1 fall is not a one-way bet


def test_holm_adjustment():
    # Sorted, the tested p-values are 0.01, 0.03, 0.04, 0.6; times 4, 3, 2, 1 that is 0.04, 0.09, 0.08, 0.6,
    # and the running maximum makes it 0.04, 0.09, 0.09, 0.6. Untested models stay NaN.
    adjusted = _holm(pd.Series([0.04, 0.01, np.nan, 0.03, 0.6]))
    assert np.allclose(adjusted, [0.09, 0.04, np.nan, 0.09, 0.6], equal_nan=True)


def make_verdict(beaten, significant, best, change, **extra) -> Verdict:
    return Verdict(beaten, significant, best, change, 500, 250, period_change=0.0, p_value=0.5, **extra)


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (
            make_verdict(True, True, "ets", -0.04),
            "ETS beat the naive forecast by 4.0%, a statistically significant margin.",
        ),
        (
            make_verdict(False, False, "ets", 0.0),
            "Nothing beat the naive forecast. The best any model managed was to match it.",
        ),
        (
            make_verdict(False, False, "theta", 0.021),
            "Nothing beat the naive forecast. The closest, Theta, was 2.1% less accurate.",
        ),
        (
            make_verdict(True, False, "drift", -0.0001),
            "Drift edged out the naive forecast by less than 0.1%, but the margin is within the noise.",
        ),
    ],
)
def test_headline(result, expected):
    assert headline(result) == expected


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (
            Verdict(True, True, "drift", -0.037, 504, 300, 0.4, 0.01, one_way="rise", one_way_origins=504),
            "It predicted a rise on all 504 days while the price rose 40%, so the win says more about this "
            "period's trend than about forecasting skill.",
        ),
        (
            Verdict(True, False, "theta", -0.01, 1000, 450, -0.12, 0.3, one_way="fall", one_way_origins=950),
            "It predicted a fall on 950 of 1,000 days while the price fell 12%, so the win says more about this "
            "period's trend than about forecasting skill.",
        ),
        # Betting on a rise and winning while the price fell is not explained by the trend.
        (Verdict(True, True, "drift", -0.05, 504, 200, -0.08, 0.01, one_way="rise", one_way_origins=504), ""),
        (make_verdict(True, True, "ets", -0.04), ""),
    ],
)
def test_trend_note(result, expected):
    assert trend_note(result) == expected
