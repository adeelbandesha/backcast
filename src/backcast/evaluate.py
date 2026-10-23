"""Walk-forward evaluation: every model is scored only on data it had not seen."""

import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from functools import cache
from itertools import repeat

import numpy as np
import pandas as pd
from scipy.stats import ttest_1samp

from .data import trading_days_after
from .models import MODELS, MODELS_BY_KEY, Forecast, Model

HORIZONS = (1, 5, 20)
WINDOWS = 30
MIN_HISTORY = 500
SIGNIFICANCE = 0.05
# A model that forecast the same direction in at least this share of windows made a one-way bet.
ONE_WAY = 0.9


@dataclass(frozen=True)
class Verdict:
    beaten: bool
    significant: bool
    best: str
    relative_change: float  # best model's error relative to naive, -0.04 means 4% lower
    windows: int
    up_windows: int  # windows in which the price rose
    period_change: float  # price change from the first forecast date to the last target date
    one_way: str | None = None  # "rise" or "fall" when the best model bet one way in nearly every window
    one_way_windows: int = 0


def walk_forward(prices: pd.Series, horizon: int, models: list[Model] = MODELS, windows: int = WINDOWS) -> pd.DataFrame:
    """Forecast from `windows` non-overlapping origins and record each model's h-day-ahead prediction.

    An origin is the number of known prices; the model sees `y[:origin]` and is scored on `y[origin + horizon - 1]`.
    """
    y = prices.to_numpy(dtype=float)
    origins = [o for o in range(len(y) - horizon, 0, -horizon)[:windows] if o >= MIN_HISTORY][::-1]
    if not origins:
        raise ValueError(f"Need at least {MIN_HISTORY + horizon} prices for a backtest, got {len(y)}.")
    forecasts = _pool().map(_forecast_endpoints, [y[:o] for o in origins], repeat(horizon), repeat(models))
    rows = [
        {
            "model": model.key,
            "origin": prices.index[origin - 1],
            "target": prices.index[origin + horizon - 1],
            "last": y[origin - 1],
            "actual": y[origin + horizon - 1],
            "predicted": predicted,
            "lower": lower,
            "upper": upper,
        }
        for origin, endpoints in zip(origins, forecasts, strict=True)
        for model, (predicted, lower, upper) in zip(models, endpoints, strict=True)
    ]
    return pd.DataFrame(rows)


@cache
def _pool() -> ProcessPoolExecutor:
    # One math-library thread per worker: the pool already uses every core, and nested pools oversubscribe them.
    # The forkserver inherits this environment when it starts, which happens on the first submit.
    for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMBA_NUM_THREADS"):
        os.environ.setdefault(variable, "1")
    # Forked children can deadlock on OpenMP state inherited from the parent; forkserver starts them clean.
    return ProcessPoolExecutor(mp_context=multiprocessing.get_context("forkserver"))


def _forecast_endpoints(known: np.ndarray, horizon: int, models: list[Model]) -> list[tuple[float, float, float]]:
    """Runs in a worker process: every model's forecast for the last day of the horizon."""
    endpoints = []
    for model in models:
        forecast = model.forecast(known, horizon)
        endpoints.append((float(forecast.mean[-1]), float(forecast.lower[-1]), float(forecast.upper[-1])))
    return endpoints


def leaderboard(results: pd.DataFrame) -> pd.DataFrame:
    """One row per model, best first. `relative_mae` below 1 means more accurate than the naive forecast."""
    results = results.assign(error=(results.actual - results.predicted).abs())
    naive_error = results[results.model == "naive"].set_index("origin").error
    rows = []
    for key, group in results.groupby("model", sort=False):
        error = group.set_index("origin").error
        improvement = naive_error - error
        moved = np.sign(group.predicted - group["last"])
        called = moved != 0  # a forecast of "no change" has no direction to score
        direction = (moved == np.sign(group.actual - group["last"]))[called].mean() if called.any() else np.nan
        rows.append(
            {
                "model": key,
                "mae": error.mean(),
                "relative_mae": error.mean() / naive_error.mean(),
                "win_rate": (improvement > 0).mean() if key != "naive" else np.nan,
                "p_value": _p_value(improvement) if key != "naive" else np.nan,
                "direction": direction,
                "coverage": group.actual.between(group.lower, group.upper).mean(),
            }
        )
    board = pd.DataFrame(rows)
    board["p_adjusted"] = _holm(board.p_value)
    return board.sort_values("relative_mae", ignore_index=True)


def _holm(p_values: pd.Series) -> pd.Series:
    """Holm's step-down adjustment for testing several models against naive at once.

    Each extra comparison is another chance of a lucky win. Adjusted p-values keep the chance of any false
    "significant" at the stated level.
    """
    tested = p_values.dropna().sort_values()
    steps = len(tested) - np.arange(len(tested))
    return (tested * steps).cummax().clip(upper=1).reindex(p_values.index)


def _p_value(improvement: pd.Series) -> float:
    if improvement.std() == 0:
        return np.nan
    return float(ttest_1samp(improvement, 0).pvalue)


def verdict(board: pd.DataFrame, results: pd.DataFrame) -> Verdict:
    best = board[board.model != "naive"].iloc[0]
    beaten = bool(best.relative_mae < 1)
    rows = results[results.model == best.model]
    moved = np.sign(rows.predicted - rows["last"])
    rises, falls = int((moved > 0).sum()), int((moved < 0).sum())
    one_way = "rise" if rises >= ONE_WAY * len(rows) else "fall" if falls >= ONE_WAY * len(rows) else None
    return Verdict(
        beaten=beaten,
        significant=bool(beaten and best.p_adjusted < SIGNIFICANCE),
        best=best.model,
        relative_change=float(best.relative_mae - 1),
        windows=len(rows),
        up_windows=int((rows.actual > rows["last"]).sum()),
        period_change=float(rows.actual.iloc[-1] / rows["last"].iloc[0] - 1),
        one_way=one_way,
        one_way_windows=max(rises, falls) if one_way else 0,
    )


def headline(result: Verdict) -> str:
    name = MODELS_BY_KEY[result.best].name
    margin = abs(result.relative_change)
    amount = f"{margin:.1%}" if margin >= 0.0005 else "less than 0.1%"
    if result.significant:
        return f"{name} beat the naive forecast by {amount}, a statistically significant margin."
    if result.beaten:
        return f"{name} edged out the naive forecast by {amount}, but the margin is within the noise."
    if np.isclose(result.relative_change, 0):
        return "Nothing beat the naive forecast. The best any model managed was to match it."
    return f"Nothing beat the naive forecast. The closest, {name}, was {amount} less accurate."


def trend_note(result: Verdict) -> str:
    """A model that bets the same way every time wins whenever the period happens to trend that way."""
    bet = {"rise": 1, "fall": -1}.get(result.one_way)
    if bet is None or np.sign(result.period_change) != bet:
        return ""
    count = f"{result.one_way_windows} of {result.windows}"
    if result.one_way_windows == result.windows:
        count = f"all {result.windows}"
    moved = "rose" if result.period_change > 0 else "fell"
    return (
        f"It predicted a {result.one_way} in {count} windows while the price {moved} {abs(result.period_change):.0%},"
        " so the win says more about this period's trend than about forecasting skill."
    )


@dataclass(frozen=True)
class Report:
    prices: pd.Series
    horizon: int
    results: pd.DataFrame
    board: pd.DataFrame
    verdict: Verdict
    future_dates: pd.DatetimeIndex
    future: dict[str, Forecast]


def run_backtest(prices: pd.Series, horizon: int, models: list[Model] = MODELS) -> Report:
    results = walk_forward(prices, horizon, models)
    board = leaderboard(results)
    future_dates, future = future_paths(prices, horizon, models)
    return Report(prices, horizon, results, board, verdict(board, results), future_dates, future)


def future_paths(prices: pd.Series, horizon: int, models: list[Model] = MODELS) -> tuple[pd.DatetimeIndex, dict]:
    """Each model's forecast from the latest price, dated by the symbol's trading calendar when it is known."""
    y = prices.to_numpy(dtype=float)
    dates = trading_days_after(prices.index[-1], horizon, prices.attrs.get("calendar"))
    return dates, {model.key: model.forecast(y, horizon) for model in models}
