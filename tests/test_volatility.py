import numpy as np
import pandas as pd
import pytest

from backcast.volatility import (
    MODELS,
    LongRun,
    Naive,
    Verdict,
    VolatilityModel,
    headline,
    leaderboard,
    long_run_note,
    run_backtest,
    walk_forward,
)


def series(returns) -> pd.Series:
    prices = 100 * np.exp(np.concatenate([[0], np.cumsum(returns)]))
    return pd.Series(prices, index=pd.bdate_range("2020-01-01", periods=len(prices)), name="TEST")


def garch_returns(n=900, seed=0, omega=2e-6, alpha=0.1, beta=0.88) -> np.ndarray:
    rng = np.random.default_rng(seed)
    variance, returns = omega / (1 - alpha - beta), np.empty(n)
    for i in range(n):
        returns[i] = np.sqrt(variance) * rng.standard_normal()
        variance = omega + alpha * returns[i] ** 2 + beta * variance
    return returns


class FitCounting(VolatilityModel):
    key, name, family, summary = "counting", "Counting", "Test", "Forecasts the number of returns it was fitted on."

    def fit(self, returns, h):
        return lambda latest: len(returns) + len(latest) / 1e6


@pytest.mark.parametrize("horizon", [1, 5, 20])
def test_models_never_see_the_returns_they_are_scored_on(horizon):
    returns = np.random.default_rng(0).normal(0, 0.01, 700)
    prices = series(returns)
    results = walk_forward(prices, horizon, [FitCounting()], origins=60, refit=7)
    origin = prices.index.get_indexer(results.origin)  # the close each forecast was made after
    fitted_on, seen = np.divmod(results.predicted.to_numpy() * 1e6, 1e6)
    assert (np.round(seen) == origin).all()  # every return up to that close, none after it
    assert (fitted_on <= origin).all() and (origin - fitted_on < 7).all()
    realized = [np.sum(returns[i : i + horizon] ** 2) for i in origin]
    assert np.allclose(results.actual, realized)
    assert prices.index.get_indexer(results.target)[-1] == len(prices) - 1


def test_naive_and_long_run_forecasts():
    returns = np.random.default_rng(1).normal(0, 0.01, 600)
    assert Naive().fit(returns, 5)(returns) == pytest.approx(5 * np.mean(returns[-20:] ** 2))
    assert LongRun().fit(returns, 5)(returns) == pytest.approx(5 * np.mean(returns**2))


@pytest.mark.parametrize("model", MODELS, ids=lambda model: model.key)
def test_every_model_forecasts_a_positive_variance_of_the_right_size(model):
    returns = garch_returns()
    variance = model.fit(returns[:800], 5)(returns)
    assert 0 < variance < 5 * 1e-2  # a positive 5-day variance below 22% daily volatility


def test_garch_beats_naive_on_garch_data_and_the_long_run_average_wins_without_clustering():
    clustered = run_backtest(series(garch_returns(1100)), 5)
    assert clustered.verdict.best in ("garch", "gjr") and clustered.verdict.beaten
    assert clustered.verdict.long_run_p_value < 0.05  # the average misses the swings that GARCH follows

    calm = run_backtest(series(np.random.default_rng(2).normal(0, 0.01, 1100)), 5)
    board = calm.board.set_index("model")
    assert board.at["long_run", "gain"] > 0  # with constant volatility, a month of data is just a noisier average
    assert board.at["long_run", "qlike"] <= board.qlike.min() + 0.01
    assert "SPA p-value that no model beats the long-run average" in calm._repr_html_()


def test_leaderboard_scores_qlike_against_naive():
    rows = []
    for day, realized in enumerate([1.0, 4.0, 2.0]):
        for key, forecast in (("naive", 1.0), ("long_run", 2.0)):
            rows.append(
                {
                    "model": key,
                    "origin": day,
                    "predicted": forecast,
                    "actual": realized,
                    "error": np.log(forecast) + realized / forecast,
                }
            )
    board = leaderboard(pd.DataFrame(rows), 1).set_index("model")
    naive, long_run = np.array([1, 4, 2]) + 0, np.log(2) + np.array([1, 4, 2]) / 2
    assert board.at["naive", "qlike"] == pytest.approx(naive.mean())
    assert board.at["long_run", "gain"] == pytest.approx((naive - long_run).mean())
    assert board.at["long_run", "win_rate"] == pytest.approx(2 / 3)
    assert board.index[0] == "long_run"


def verdict(**changes) -> Verdict:
    fields = dict(
        best="gjr", beaten=True, significant=True, p_value=0.01, long_run_p_value=0.3, origins=504, realized=0.2
    )
    return Verdict(**{**fields, **changes})


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (verdict(), "GJR-GARCH forecast volatility better than naive, by a statistically significant margin."),
        (verdict(significant=False), "GJR-GARCH forecast volatility better than naive, but the margin is within"),
        (verdict(beaten=False, significant=False), "Nothing forecast volatility better than naive. The closest"),
    ],
)
def test_headline(result, expected):
    assert headline(result).startswith(expected)


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (verdict(), "no model did better by more than luck would."),
        (verdict(long_run_p_value=0.01), "at least one model did better by more than luck would."),
        (verdict(best="long_run"), "The best forecast was the long-run average itself"),
    ],
)
def test_long_run_note(result, expected):
    assert expected in long_run_note(result)
