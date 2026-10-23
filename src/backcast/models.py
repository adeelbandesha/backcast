"""Forecasting models.

Every model turns a price history into an h-step forecast path with an 80% interval.
To add one, subclass `Model`, implement `forecast`, and append an instance to `MODELS`.
"""

from dataclasses import dataclass
from functools import partial

import lightgbm as lgb
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from scipy.stats import norm
from statsforecast.models import AutoARIMA, AutoETS, AutoTheta

LEVEL = 80
Z = norm.ppf(0.5 + LEVEL / 200)


@dataclass(frozen=True)
class Forecast:
    mean: np.ndarray
    lower: np.ndarray
    upper: np.ndarray


class Model:
    key: str
    name: str
    family: str
    summary: str

    def forecast(self, y: np.ndarray, h: int) -> Forecast:
        raise NotImplementedError


class Naive(Model):
    key, name, family = "naive", "Naive", "Baseline"
    summary = "The price in h days equals today's price. The benchmark every other model has to beat."

    def forecast(self, y, h):
        steps = np.arange(1, h + 1)
        mean = np.full(h, y[-1])
        spread = Z * np.diff(y).std(ddof=1) * np.sqrt(steps)
        return Forecast(mean, mean - spread, mean + spread)


class Drift(Model):
    key, name, family = "drift", "Drift", "Baseline"
    summary = "Extends the average daily change over the training window, like a line from the first price to the last."

    def forecast(self, y, h):
        steps = np.arange(1, h + 1)
        diffs = np.diff(y)
        mean = y[-1] + diffs.mean() * steps
        spread = Z * diffs.std(ddof=1) * np.sqrt(steps * (1 + steps / len(diffs)))
        return Forecast(mean, mean - spread, mean + spread)


class StatsForecastModel(Model):
    family = "Statistical"

    def __init__(self, key, name, summary, build):
        self.key, self.name, self.summary, self._build = key, name, summary, build

    def forecast(self, y, h):
        result = self._build().forecast(y=y.astype(float), h=h, level=[LEVEL])
        mean, lower, upper = (np.asarray(result[k], dtype=float) for k in ("mean", f"lo-{LEVEL}", f"hi-{LEVEL}"))
        return Forecast(mean, lower, upper)


class GradientBoosting(Model):
    key, name, family = "lightgbm", "LightGBM", "Machine learning"
    summary = (
        "Gradient-boosted trees that read the last 20 daily returns and predict the return over the next h days, "
        "with quantile regression for the interval. The path between today and day h is interpolated."
    )
    lags = 20

    def forecast(self, y, h):
        log_y = np.log(y)
        n = len(log_y)
        windows = sliding_window_view(np.diff(log_y), self.lags)  # row k holds the returns ending at day k + lags
        features, latest = windows[: n - h - self.lags], windows[-1:]
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
        returns = sorted(
            lgb.train({**params, "alpha": alpha}, lgb.Dataset(features, target), num_boost_round=100).predict(latest)[0]
            for alpha in (0.1, 0.5, 0.9)
        )
        fraction = np.arange(1, h + 1) / h
        lower, mid, upper = (y[-1] * np.exp(r * fraction) for r in returns)
        return Forecast(mid, lower, upper)


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
