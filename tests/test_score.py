import numpy as np
import pandas as pd
import pytest

from backcast import cli
from backcast.evaluate import MIN_ORIGINS, headline, leaderboard, score, walk_forward
from backcast.models import Drift, Naive


def random_walk(n=700, seed=0) -> pd.Series:
    returns = np.random.default_rng(seed).normal(0, 0.01, n)
    return pd.Series(100 * np.exp(np.cumsum(returns)), index=pd.bdate_range("2020-01-01", periods=n), name="TEST")


@pytest.fixture(scope="module")
def backtest():
    prices = random_walk()
    return prices, walk_forward(prices, 5, [Naive(), Drift()])


def drift_forecasts(results, interval=True) -> pd.DataFrame:
    drift = results[results.model == "drift"]
    forecasts = pd.DataFrame({"date": drift.origin, "forecast": drift.predicted})
    return forecasts.assign(q10=drift.lower, q90=drift.upper) if interval else forecasts


def test_scoring_a_backtests_own_forecasts_reproduces_it(backtest):
    prices, results = backtest
    report = score(drift_forecasts(results), prices, 5, name="drift")

    expected = results.drop(columns="crps").sort_values(["origin", "model"], ignore_index=True)
    pd.testing.assert_frame_equal(report.results.drop(columns="crps"), expected, check_dtype=False)
    naive = results[results.model == "naive"].reset_index(drop=True)
    assert np.allclose(report.results[report.results.model == "naive"].crps, naive.crps)
    columns = ["model", "mae", "relative_mae", "relative_interval", "win_rate", "p_value", "direction", "coverage"]
    pd.testing.assert_frame_equal(report.board[columns], leaderboard(results, 5)[columns])
    assert report.verdict.origins == len(naive)


def test_a_point_forecast_has_no_interval_scores(backtest):
    prices, results = backtest
    board = score(drift_forecasts(results, interval=False), prices, 5).board.set_index("model")
    assert np.isnan(board.loc["Your forecasts", ["relative_crps", "relative_interval", "coverage"]]).all()
    assert board.loc["naive", "coverage"] > 0


def test_forecasts_still_waiting_for_their_target_are_left_out(backtest):
    prices, results = backtest
    forecasts = drift_forecasts(results)
    pending = pd.DataFrame({"date": prices.index[-3:], "forecast": 100.0, "q10": 90.0, "q90": 110.0})
    report = score(pd.concat([forecasts, pending]), prices, 5)
    assert report.verdict.origins == len(forecasts)


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda f, p: f.rename(columns={"forecast": "price"}), "need a forecast column"),
        (
            lambda f, p: f.assign(date=f.date.where(f.date != f.date.max(), pd.Timestamp("2021-06-05"))),
            "isn't a trading day",
        ),
        (lambda f, p: pd.concat([f, f.tail(1)]), "more than one forecast"),
        (lambda f, p: f.head(MIN_ORIGINS - 1), f"at least {MIN_ORIGINS} forecasts"),
        (lambda f, p: f.assign(forecast=-1.0), "invalid forecast"),
        (lambda f, p: f.assign(q10=f.q90 + 1), "invalid forecast"),
        (lambda f, p: pd.concat([pd.DataFrame({"date": p.index[:1], "forecast": 1.0}), f]), "prices need to start"),
    ],
)
def test_bad_forecasts_are_refused(backtest, change, message):
    prices, results = backtest
    with pytest.raises(ValueError, match=message):
        score(change(drift_forecasts(results), prices), prices, 5)


def test_the_headline_names_the_scored_forecasts(backtest):
    prices, results = backtest
    worse = drift_forecasts(results, interval=False).assign(forecast=lambda f: f.forecast * 1.05)
    report = score(worse, prices, 5)
    assert headline(report.verdict).startswith("Your forecasts didn't beat the naive forecast: the error was")


def test_naive_is_taken():
    with pytest.raises(ValueError, match="benchmark"):
        score(pd.DataFrame(), random_walk(), 5, name="naive")


def test_the_command_line_scores_a_csv(backtest, tmp_path, monkeypatch, capsys):
    prices, results = backtest
    path = tmp_path / "forecasts.csv"
    drift_forecasts(results).to_csv(path, index=False)
    monkeypatch.setattr(cli, "load_prices", lambda symbol: prices)

    assert cli.main(["score", str(path), "TEST", "--horizon", "5"]) == 0
    printed = capsys.readouterr().out
    assert f"{len(results) // 2} of your 5-day forecasts" in printed
    assert "Your forecasts" in printed and "Naive" in printed

    assert cli.main(["score", str(tmp_path / "missing.csv"), "TEST"]) == 1
    assert "missing.csv" in capsys.readouterr().err
