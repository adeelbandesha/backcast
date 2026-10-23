import numpy as np
import pandas as pd
import pytest
from scipy.stats import ttest_1samp

from backcast.evaluate import Verdict, _holm, headline, leaderboard, trend_note, verdict, walk_forward
from backcast.models import Drift, Forecast, Model, Naive


def series(values) -> pd.Series:
    return pd.Series(values, index=pd.bdate_range("2020-01-01", periods=len(values)), name="TEST")


def random_walk(n=700, seed=0) -> pd.Series:
    return series(100 * np.exp(np.cumsum(np.random.default_rng(seed).normal(0, 0.01, n))))


def window_rows(model, last, actual, predicted, spread=1.0) -> pd.DataFrame:
    """Walk-forward results written out by hand, one row per window."""
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
        }
    )


class CountingModel(Model):
    key, name, family, summary = "spy", "Spy", "Test", "Forecasts the number of prices it was shown."

    def forecast(self, y, h):
        seen = np.full(h, float(len(y)))
        return Forecast(seen, seen, seen)


def test_naive_forecasts_the_last_known_price():
    prices = random_walk()
    results = walk_forward(prices, 5, [Naive()])
    assert len(results) == 30
    assert np.allclose(results.predicted, results["last"])
    assert (results.actual.to_numpy() == prices.loc[results.target].to_numpy()).all()


@pytest.mark.parametrize("horizon", [1, 5, 20])
def test_models_never_see_the_price_they_are_scored_on(horizon):
    prices = random_walk()
    results = walk_forward(prices, horizon, [CountingModel()])
    origin = prices.index.get_indexer(results.origin)
    target = prices.index.get_indexer(results.target)
    assert (results.predicted == origin + 1).all()  # saw prices up to and including the origin
    assert (target == origin + horizon).all()
    assert (np.diff(origin) == horizon).all()  # windows don't overlap


def test_too_little_history_is_an_error():
    with pytest.raises(ValueError, match="Need at least"):
        walk_forward(random_walk(400), 5, [Naive()])


def test_drift_wins_on_a_trend_and_the_headline_says_why():
    noise = np.random.default_rng(1).normal(0, 0.3, 700)
    results = walk_forward(series(100 + 0.5 * np.arange(700) + noise), 5, [Naive(), Drift()])
    result = verdict(leaderboard(results), results)
    assert result.best == "drift" and result.beaten and result.significant
    assert result.one_way == "rise" and result.one_way_windows == 30
    assert trend_note(result).startswith("It predicted a rise in all 30 windows while the price rose")


def test_leaderboard_metrics():
    last = [100.0] * 4
    actual = [102.0, 98.0, 101.0, 105.0]
    results = pd.concat(
        [
            window_rows("naive", last, actual, last, spread=1.0),
            window_rows("drift", last, actual, [101.0, 99.0, 103.0, 104.0], spread=1.5),
        ]
    )
    board = leaderboard(results).set_index("model")

    assert list(board.index) == ["drift", "naive"]
    assert board.loc["naive", "mae"] == pytest.approx(2.5)
    assert board.loc["drift", "mae"] == pytest.approx(1.25)
    assert board.loc["drift", "relative_mae"] == pytest.approx(0.5)
    assert board.loc["drift", "win_rate"] == pytest.approx(0.75)
    assert board.loc["drift", "p_value"] == pytest.approx(ttest_1samp([1, 1, -1, 4], 0).pvalue)
    assert board.loc["drift", "p_adjusted"] == board.loc["drift", "p_value"]  # one model tested, nothing to adjust
    assert board.loc["drift", "direction"] == pytest.approx(1.0)
    assert np.isnan(board.loc["naive", "direction"])  # "no change" has no direction
    assert board.loc["drift", "coverage"] == pytest.approx(0.75)
    assert board.loc["naive", "coverage"] == pytest.approx(0.25)

    result = verdict(board.reset_index(), results)
    assert result.beaten and not result.significant
    assert (result.windows, result.up_windows, result.period_change) == (4, 3, pytest.approx(0.05))
    assert result.one_way is None  # 3 rises and 1 fall is not a one-way bet
    assert headline(result) == "Drift edged out the naive forecast by 50.0%, but the margin is within the noise."


def test_holm_adjustment():
    # Sorted, the tested p-values are 0.01, 0.03, 0.04, 0.6; times 4, 3, 2, 1 that is 0.04, 0.09, 0.08, 0.6,
    # and the running maximum makes it 0.04, 0.09, 0.09, 0.6. Untested models stay NaN.
    adjusted = _holm(pd.Series([0.04, 0.01, np.nan, 0.03, 0.6]))
    assert np.allclose(adjusted, [0.09, 0.04, np.nan, 0.09, 0.6], equal_nan=True)


def make_verdict(beaten, significant, best, change, **extra) -> Verdict:
    return Verdict(beaten, significant, best, change, windows=30, up_windows=15, period_change=0.0, **extra)


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
            Verdict(True, True, "drift", -0.037, 30, 23, 0.4, one_way="rise", one_way_windows=30),
            "It predicted a rise in all 30 windows while the price rose 40%, so the win says more about this "
            "period's trend than about forecasting skill.",
        ),
        (
            Verdict(True, False, "theta", -0.01, 30, 9, -0.12, one_way="fall", one_way_windows=28),
            "It predicted a fall in 28 of 30 windows while the price fell 12%, so the win says more about this "
            "period's trend than about forecasting skill.",
        ),
        # Betting on a rise and winning while the price fell is not explained by the trend.
        (Verdict(True, True, "drift", -0.05, 30, 12, -0.08, one_way="rise", one_way_windows=30), ""),
        (make_verdict(True, True, "ets", -0.04), ""),
    ],
)
def test_trend_note(result, expected):
    assert trend_note(result) == expected
