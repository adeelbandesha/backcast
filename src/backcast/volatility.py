"""Volatility forecasts: how much the price will move over the next h trading days, whichever way it goes.

Every model forecasts the variance of the h-day log return. Each forecast is scored against the realized variance,
the sum of the squared daily log returns over those h days, with the QLIKE loss, log(f) + realized / f. One day's
squared return is a very noisy measure of that day's variance, but QLIKE still ranks forecasts as the true variance
would (Patton, 2011), which squared error on volatility does not. Lower is better. The walk-forward schedule, the
Diebold-Mariano test and the SPA test are the price backtest's (see evaluate.py).
"""

from collections.abc import Callable
from dataclasses import dataclass
from itertools import chain, repeat

import numpy as np
import pandas as pd
from arch import arch_model
from scipy.optimize import nnls

from .evaluate import (
    MIN_HISTORY,
    ORIGINS,
    REFIT,
    SIGNIFICANCE,
    _diebold_mariano,
    _holm,
    _pool,
    required_prices,
    superior_predictive_ability,
)

TRADING_DAYS = 252


class VolatilityModel:
    key: str
    name: str
    family: str
    summary: str

    def fit(self, returns: np.ndarray, h: int) -> Callable[[np.ndarray], float]:
        """Estimate on `returns` and return a function that forecasts the variance of the next h days' log return
        from any later history, keeping those estimates.
        """
        raise NotImplementedError


class Naive(VolatilityModel):
    key, name, family = "naive", "Naive", "Baseline"
    summary = (
        "The next h days will be as volatile as the last 20 trading days were. This is the historical volatility "
        "traders quote, and the benchmark."
    )
    window = 20

    def fit(self, returns, h):
        return lambda latest: h * np.mean(latest[-self.window :] ** 2)


class LongRun(VolatilityModel):
    key, name, family = "long_run", "Long-run average", "Baseline"
    summary = (
        "The next h days will be as volatile as all the history so far, on average. Volatility tends to drift back "
        "to its average, so on most stocks this is a harder baseline than naive."
    )

    def fit(self, returns, h):
        return lambda latest: h * np.mean(latest**2)


class EWMA(VolatilityModel):
    key, name, family = "ewma", "EWMA", "Smoothing"
    summary = (
        "RiskMetrics' exponentially weighted average of squared returns, each day weighted 0.94 times the day after "
        "it. Recent days count most, but no window cuts off."
    )
    decay = 0.94

    def fit(self, returns, h):
        def forecast(latest):
            weights = self.decay ** np.arange(len(latest))[::-1]
            return h * (weights @ latest**2) / weights.sum()

        return forecast


class GARCH(VolatilityModel):
    family = "GARCH"

    def __init__(self, key, name, summary, asymmetric):
        self.key, self.name, self.summary, self.asymmetric = key, name, summary, asymmetric

    def _model(self, returns):
        # Percent returns: arch's optimiser struggles with daily variances around 1e-4.
        return arch_model(100 * returns, vol="GARCH", p=1, o=int(self.asymmetric), q=1, rescale=False)

    def fit(self, returns, h):
        params = self._model(returns).fit(disp="off").params

        def forecast(latest):
            result = self._model(latest).fix(params).forecast(horizon=h, reindex=False)
            return result.variance.to_numpy()[-1].sum() / 100**2

        return forecast


class HAR(VolatilityModel):
    key, name, family = "har", "HAR", "Regression"
    summary = (
        "Corsi's heterogeneous autoregression: the next h days' variance regressed on the average squared return "
        "over the last day, week and month. The coefficients are kept non-negative, so no forecast is below zero."
    )
    windows = (1, 5, 22)

    def _features(self, squared):
        """One row per day with a month behind it: a constant and the mean squared return over each window."""
        total = np.concatenate([[0], np.cumsum(squared)])
        ends = np.arange(self.windows[-1], len(squared) + 1)
        return ends, np.column_stack([np.ones(len(ends))] + [(total[ends] - total[ends - w]) / w for w in self.windows])

    def fit(self, returns, h):
        squared = returns**2
        ends, features = self._features(squared)
        known = ends + h <= len(squared)  # rows whose next h days are in the training data
        total = np.concatenate([[0], np.cumsum(squared)])
        coefficients, _ = nnls(features[known], total[ends[known] + h] - total[ends[known]])
        return lambda latest: self._features(latest[-self.windows[-1] :] ** 2)[1][-1] @ coefficients


MODELS: list[VolatilityModel] = [
    Naive(),
    LongRun(),
    EWMA(),
    GARCH(
        "garch",
        "GARCH(1,1)",
        "Bollerslev's GARCH: today's variance is a weighted mix of the long-run variance, yesterday's variance and "
        "yesterday's squared return. Fitted by maximum likelihood.",
        asymmetric=False,
    ),
    GARCH(
        "gjr",
        "GJR-GARCH",
        "GARCH with an extra term for falls, since a drop usually raises volatility more than a rise of the same "
        "size (Glosten, Jagannathan and Runkle).",
        asymmetric=True,
    ),
    HAR(),
]

MODELS_BY_KEY = {model.key: model for model in MODELS}


def walk_forward(
    prices: pd.Series,
    horizon: int,
    models: list[VolatilityModel] = MODELS,
    origins: int = ORIGINS,
    refit: int = REFIT,
) -> pd.DataFrame:
    """Forecast the next h days' variance from each of the last `origins` trading days and score it.

    As in the price backtest, an origin is the number of known prices. The models see the returns up to that close,
    and the realized variance is the sum of the h squared daily returns after it.
    """
    y = prices.to_numpy(dtype=float)
    if len(y) < required_prices(horizon):
        raise ValueError(f"Need at least {required_prices(horizon)} prices for a backtest, got {len(y)}.")
    returns = np.diff(np.log(y))  # returns[i] is the move from close i to close i + 1
    last = len(y) - horizon
    first = max(MIN_HISTORY, last - origins + 1)
    blocks = [range(start, min(start + refit, last + 1)) for start in range(first, last + 1, refit)]
    forecasts = _pool().map(
        _forecast_block, [returns[: block[-1] - 1] for block in blocks], blocks, repeat(horizon), repeat(models)
    )
    rows = []
    for origin, variances in zip(chain(*blocks), chain(*forecasts), strict=True):
        realized = float(np.sum(returns[origin - 1 : origin - 1 + horizon] ** 2))
        for model, variance in zip(models, variances, strict=True):
            if not (np.isfinite(variance) and variance > 0):
                raise ValueError(
                    f"{model.name} gave an invalid forecast on {prices.index[origin - 1]:%d %b %Y}: a variance must "
                    "be a positive number."
                )
            rows.append(
                {
                    "model": model.key,
                    "origin": prices.index[origin - 1],
                    "target": prices.index[origin + horizon - 1],
                    "predicted": variance,
                    "actual": realized,
                    "error": np.log(variance) + realized / variance,  # QLIKE
                }
            )
    return pd.DataFrame(rows)


def _forecast_block(known: np.ndarray, origins: range, horizon: int, models: list[VolatilityModel]) -> list[list]:
    """Runs in a worker process. `known` holds the returns up to the block's last origin, and no later ones."""
    forecasters = [model.fit(known[: origins[0] - 1], horizon) for model in models]
    return [[float(forecaster(known[: origin - 1])) for forecaster in forecasters] for origin in origins]


def annualized(variance, horizon: int):
    """An h-day variance as annualized volatility, the way volatility is usually quoted."""
    return np.sqrt(np.asarray(variance) * TRADING_DAYS / horizon)


def leaderboard(results: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """One row per model, lowest loss first. A positive `gain` is how much lower its QLIKE was than naive's."""
    naive = results[results.model == "naive"].set_index("origin")
    rows = []
    for key, group in results.groupby("model", sort=False):
        improvement = naive.error - group.set_index("origin").error
        rows.append(
            {
                "model": key,
                "qlike": group.error.mean(),
                "gain": improvement.mean(),
                "win_rate": (improvement > 0).mean() if key != "naive" else np.nan,
                "p_value": _diebold_mariano(improvement.to_numpy(), horizon) if key != "naive" else np.nan,
                "volatility": float(annualized(group.predicted.mean(), horizon)),
            }
        )
    board = pd.DataFrame(rows)
    board["p_adjusted"] = _holm(board.p_value)
    return board.sort_values("qlike", ignore_index=True)


@dataclass(frozen=True)
class Verdict:
    best: str
    beaten: bool
    significant: bool
    p_value: float  # SPA: could the best result among the models, against naive, be luck?
    long_run_p_value: float  # the same against the long-run average
    origins: int
    realized: float  # annualized volatility over the scored period


def verdict(board: pd.DataFrame, results: pd.DataFrame, horizon: int) -> Verdict:
    best = board[board.model != "naive"].iloc[0]
    beaten = bool(best.gain > 0)
    p_value = superior_predictive_ability(results, horizon)
    naive = results[results.model == "naive"]
    return Verdict(
        best=best.model,
        beaten=beaten,
        significant=bool(beaten and p_value < SIGNIFICANCE),
        p_value=p_value,
        long_run_p_value=superior_predictive_ability(results, horizon, benchmark="long_run"),
        origins=len(naive),
        realized=float(annualized(naive.actual.mean(), horizon)),
    )


def headline(result: Verdict) -> str:
    name = MODELS_BY_KEY[result.best].name
    if result.significant:
        return f"{name} forecast volatility better than naive, by a statistically significant margin."
    if result.beaten:
        return f"{name} forecast volatility better than naive, but the margin is within the noise."
    return f"Nothing forecast volatility better than naive. The closest was {name}."


def long_run_note(result: Verdict) -> str:
    """The second baseline: naive only looks at the last month, and volatility tends to drift back to its average."""
    if result.best == "long_run":
        return (
            "The best forecast was the long-run average itself: no model that follows recent volatility did better "
            "than assuming it stays at its average."
        )
    outcome = "at least one model" if result.long_run_p_value < SIGNIFICANCE else "no model"
    return (
        f"Against the long-run average volatility, which bets that volatility drifts back to its average, {outcome} "
        "did better by more than luck would."
    )


@dataclass(frozen=True)
class Report:
    prices: pd.Series
    horizon: int
    results: pd.DataFrame
    board: pd.DataFrame
    verdict: Verdict


def run_backtest(prices: pd.Series, horizon: int, models: list[VolatilityModel] = MODELS) -> Report:
    results = walk_forward(prices, horizon, models)
    board = leaderboard(results, horizon)
    return Report(prices, horizon, results, board, verdict(board, results, horizon))
