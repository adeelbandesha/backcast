"""Forecasting models.

Every model turns a price history into an h-step forecast path with quantiles from 10% to 90%; the outer pair is the
80% interval. The models work on log prices and exponentiate their forecasts: prices can't fall below zero and move in
proportion to their level, and a quantile stays a quantile through the exponential, so the point forecast is the
median. To add one, subclass `Model`, implement `forecast` (or `fit`, if estimating is slow), and append an
instance to `MODELS`.
"""

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

import lightgbm as lgb
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from scipy.stats import norm
from statsforecast.models import AutoARIMA, AutoETS, AutoTheta

LEVEL = 80
QUANTILES = np.linspace(0.1, 0.9, 9)
Z = norm.ppf(QUANTILES)[:, None]  # standard normal quantiles, one row per forecast quantile


@dataclass(frozen=True)
class Forecast:
    median: np.ndarray  # the point forecast, shape (h,)
    quantiles: np.ndarray  # shape (len(QUANTILES), h)

    @property
    def lower(self) -> np.ndarray:
        return self.quantiles[0]

    @property
    def upper(self) -> np.ndarray:
        return self.quantiles[-1]


class Model:
    """Subclasses implement `forecast`, or `fit` when estimating is slow enough to be worth reusing."""

    key: str
    name: str
    family: str
    summary: str

    def forecast(self, y: np.ndarray, h: int) -> Forecast:
        return self.fit(y, h)(y)

    def fit(self, y: np.ndarray, h: int) -> Callable[[np.ndarray], Forecast]:
        """Estimate on `y` and return a forecaster for any later history (one that extends `y`) that keeps those
        estimates. The default re-estimates every time, which is right for models that cost nothing to fit."""
        return lambda latest: self.forecast(latest, h)


class Naive(Model):
    key, name, family = "naive", "Naive", "Baseline"
    summary = "The price in h days equals today's price. The benchmark every other model has to beat."

    def forecast(self, y, h):
        steps = np.arange(1, h + 1)
        returns = np.diff(np.log(y))
        return Forecast(np.full(h, y[-1]), y[-1] * np.exp(Z * returns.std(ddof=1) * np.sqrt(steps)))


class Drift(Model):
    key, name, family = "drift", "Drift", "Baseline"
    summary = "Extends the average daily percentage change over the training window into the future."

    def forecast(self, y, h):
        steps = np.arange(1, h + 1)
        returns = np.diff(np.log(y))
        log_median = np.log(y[-1]) + returns.mean() * steps
        spread = returns.std(ddof=1) * np.sqrt(steps * (1 + steps / len(returns)))
        return Forecast(np.exp(log_median), np.exp(log_median + Z * spread))


class StatsForecastModel(Model):
    family = "Statistical"
    # statsforecast returns symmetric intervals: the 20% one spans the 40% and 60% quantiles, and so on.
    levels = [round(200 * q - 100) for q in QUANTILES if q > 0.5]
    columns = [f"lo-{level}" for level in reversed(levels)] + ["mean"] + [f"hi-{level}" for level in levels]

    def __init__(self, key, name, summary, build):
        self.key, self.name, self.summary, self._build = key, name, summary, build

    def fit(self, y, h):
        model = self._build().fit(y=np.log(y))

        def forecast(latest):
            # `forward` runs the fitted parameters over the longer history without estimating them again.
            result = model.forward(y=np.log(latest), h=h, level=self.levels)
            quantiles = np.exp([np.asarray(result[column], dtype=float) for column in self.columns])
            return Forecast(np.exp(np.asarray(result["mean"], dtype=float)), quantiles)

        return forecast


class GradientBoosting(Model):
    key, name, family = "lightgbm", "LightGBM", "Machine learning"
    summary = (
        "Gradient-boosted trees that read the last 20 daily returns and predict the return over the next h days, "
        "one quantile regression per forecast quantile. The path between today and day h is interpolated."
    )
    lags = 20

    def fit(self, y, h):
        log_y = np.log(y)
        n = len(log_y)
        windows = sliding_window_view(np.diff(log_y), self.lags)  # row k holds the returns ending at day k + lags
        features = windows[: n - h - self.lags]
        target = log_y[self.lags + h :] - log_y[self.lags : n - h]
        params = {
            "objective": "quantile",
            "learning_rate": 0.08,
            "num_leaves": 15,
            "min_data_in_leaf": 20,
            "max_bin": 63,
            "num_threads": 1,  # parallelism comes from the backtest worker pool
            "verbose": -1,
        }
        boosters = [
            lgb.train({**params, "alpha": q}, lgb.Dataset(features, target), num_boost_round=100) for q in QUANTILES
        ]
        fraction = np.arange(1, h + 1) / h

        def forecast(latest):
            x = np.diff(np.log(latest[-self.lags - 1 :]))[None]
            returns = np.sort([booster.predict(x)[0] for booster in boosters])  # separate fits can cross; sort them
            quantiles = latest[-1] * np.exp(np.outer(returns, fraction))
            return Forecast(quantiles[len(QUANTILES) // 2], quantiles)

        return forecast


MODELS: list[Model] = [
    Naive(),
    Drift(),
    StatsForecastModel(
        "ets",
        "ETS",
        "Exponential smoothing with automatic selection of error, trend and seasonality components.",
        partial(AutoETS, season_length=1),
    ),
    StatsForecastModel(
        "arima",
        "ARIMA",
        "Autoregressive integrated moving average, with orders chosen automatically by information criterion.",
        partial(AutoARIMA, season_length=1),
    ),
    StatsForecastModel(
        "theta",
        "Theta",
        "The Theta method, a strong performer in the M3 forecasting competition, with automatic variant selection.",
        partial(AutoTheta, season_length=1),
    ),
    GradientBoosting(),
]

MODELS_BY_KEY = {model.key: model for model in MODELS}
