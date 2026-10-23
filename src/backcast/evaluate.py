"""Walk-forward evaluation: every model is scored only on data it had not seen."""

import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from functools import cache
from itertools import chain, repeat

import numpy as np
import pandas as pd
from arch.bootstrap import SPA
from scipy.stats import t

from .data import trading_days_after
from .models import LEVEL, MODELS, MODELS_BY_KEY, QUANTILES, Forecast, Model

HORIZONS = (1, 5, 20)
ORIGINS = 504  # about two years of trading days, one forecast origin per day
REFIT = 20  # trading days between re-estimates; see `walk_forward`
MIN_HISTORY = 500
MIN_ORIGINS = 100
TREND_DAYS = 200  # an origin is in an uptrend when the close is above its average over this many days
SIGNIFICANCE = 0.05
# A model that forecast the same direction from at least this share of origins made a one-way bet.
ONE_WAY = 0.9


@dataclass(frozen=True)
class Verdict:
    beaten: bool
    significant: bool
    best: str
    relative_change: float  # best model's error relative to naive, -0.04 means 4% lower
    origins: int
    up_origins: int  # origins after which the price rose over the horizon
    period_change: float  # price change from the first forecast date to the last target date
    p_value: float  # Hansen's SPA: could the best result among all the models be luck?
    one_way: str | None = None  # "rise" or "fall" when the best model bet one way from nearly every origin
    one_way_origins: int = 0


def walk_forward(
    prices: pd.Series, horizon: int, models: list[Model] = MODELS, origins: int = ORIGINS, refit: int = REFIT
) -> pd.DataFrame:
    """Forecast from each of the last `origins` trading days and score every model's h-day-ahead prediction.

    An origin is the number of known prices; the model sees `y[:origin]` and is scored on `y[origin + horizon - 1]`.
    Models are estimated at every `refit`-th origin and keep those estimates, seeing only the new prices, until the
    next one. Measured on AAPL, KO and NVDA at every horizon, refitting daily instead moved each model's error
    relative to naive by 0.2 percentage points on average (1.6 at most), changed no significance verdict, and took
    16 times as long. Refitting every 5 days came no closer to daily than every 20.
    Scores are on log prices, so a 2% miss counts the same at any price level.
    """
    y = prices.to_numpy(dtype=float)
    if len(y) < required_prices(horizon):
        raise ValueError(f"Need at least {required_prices(horizon)} prices for a backtest, got {len(y)}.")
    last = len(y) - horizon
    first = max(MIN_HISTORY, last - origins + 1)
    blocks = [range(start, min(start + refit, last + 1)) for start in range(first, last + 1, refit)]
    forecasts = _pool().map(
        _forecast_block, [y[: block[-1]] for block in blocks], blocks, repeat(horizon), repeat(models)
    )
    average = prices.rolling(TREND_DAYS).mean().to_numpy()
    rows = []
    for origin, endpoints in zip(chain(*blocks), chain(*forecasts), strict=True):
        actual = np.log(y[origin + horizon - 1])
        for model, (predicted, quantiles) in zip(models, endpoints, strict=True):
            # shortcut: a normal model can put a quantile below zero on a wildly volatile asset; its log is NaN and
            # that model's scores show as missing. Score such forecasts explicitly if they ever turn up.
            quantiles = np.log(quantiles)
            rows.append(
                {
                    "model": model.key,
                    "origin": prices.index[origin - 1],
                    "target": prices.index[origin + horizon - 1],
                    "last": y[origin - 1],
                    "actual": y[origin + horizon - 1],
                    "predicted": predicted,
                    "lower": np.exp(quantiles[0]),
                    "upper": np.exp(quantiles[-1]),
                    "error": abs(actual - np.log(predicted)),
                    "crps": _crps(quantiles, actual),
                    "interval": _interval_score(quantiles[0], quantiles[-1], actual),
                    "uptrend": y[origin - 1] > average[origin - 1],
                }
            )
    return pd.DataFrame(rows)


def required_prices(horizon: int) -> int:
    """Training history for the first origin, then enough origins for the significance tests to mean something."""
    return MIN_HISTORY + MIN_ORIGINS - 1 + horizon


def _crps(quantiles: np.ndarray, actual: float) -> float:
    """CRPS approximated by twice the mean pinball loss over the forecast quantiles. For a point forecast it equals
    the absolute error. The quantiles stop at 10% and 90%, so a miss far into the tails costs no more than a near
    one here; the interval score is the metric that charges for it."""
    return float(2 * np.mean(((actual < quantiles) - QUANTILES) * (quantiles - actual)))


def _interval_score(lower: float, upper: float, actual: float) -> float:
    """Gneiting and Raftery's interval score: the interval's width, plus 2/alpha times any distance by which the
    actual falls outside it. Lower is better; a narrow interval only pays off if it also holds the actual."""
    alpha = 1 - LEVEL / 100
    return upper - lower + 2 / alpha * (max(lower - actual, 0) + max(actual - upper, 0))


@cache
def _pool() -> ProcessPoolExecutor:
    # One math-library thread per worker: the pool already uses every core, and nested pools oversubscribe them.
    # The forkserver inherits this environment when it starts, which happens on the first submit.
    for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMBA_NUM_THREADS"):
        os.environ.setdefault(variable, "1")
    # Forked children can deadlock on OpenMP state inherited from the parent; forkserver starts them clean.
    return ProcessPoolExecutor(mp_context=multiprocessing.get_context("forkserver"))


def _forecast_block(known: np.ndarray, origins: range, horizon: int, models: list[Model]) -> list[list[tuple]]:
    """Runs in a worker process: fit every model at the block's first origin, then forecast the horizon's last day
    from each origin in the block. `known` ends at the block's last origin, so no model can see a later price."""
    forecasters = [model.fit(known[: origins[0]], horizon) for model in models]
    endpoints = []
    for origin in origins:
        forecasts = [forecaster(known[:origin]) for forecaster in forecasters]
        endpoints.append([(float(f.mean[-1]), f.quantiles[:, -1]) for f in forecasts])
    return endpoints


def leaderboard(results: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """One row per model, best first. `relative_mae` below 1 means more accurate than the naive forecast."""
    naive = results[results.model == "naive"].set_index("origin")
    rows = []
    for key, group in results.groupby("model", sort=False):
        group = group.set_index("origin")
        improvement = naive.error - group.error
        moved = np.sign(group.predicted - group["last"])
        called = moved != 0  # a forecast of "no change" has no direction to score
        direction = (moved == np.sign(group.actual - group["last"]))[called].mean() if called.any() else np.nan
        rows.append(
            {
                "model": key,
                "mae": group.error.mean(),
                "relative_mae": group.error.mean() / naive.error.mean(),
                "relative_crps": group.crps.mean() / naive.crps.mean(),
                "relative_interval": group.interval.mean() / naive.interval.mean(),
                "win_rate": (improvement > 0).mean() if key != "naive" else np.nan,
                "p_value": _diebold_mariano(improvement.to_numpy(), horizon) if key != "naive" else np.nan,
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


def _diebold_mariano(improvement: np.ndarray, horizon: int) -> float:
    """Two-sided p-value for "the mean loss difference is zero" (Diebold and Mariano, 1995).

    Forecasts made on consecutive days share h - 1 days of their future, so their errors are correlated over h - 1
    lags. The variance adds those autocovariances with equal weights, as in the original test, with Harvey,
    Leybourne and Newbold's (1997) small-sample correction and a t distribution. At h = 1 this is exactly the paired
    t-test. In simulations at 504 origins it rejects a true null 5-7% of the time at the 5% level, where Newey-West
    weights reject 10-11%; those are kept as the fallback for the rare sample whose variance comes out negative.
    """
    n = len(improvement)
    centred = improvement - improvement.mean()
    autocovariances = [centred[lag:] @ centred[: n - lag] / n for lag in range(min(horizon, n))]
    variance = autocovariances[0] + 2 * sum(autocovariances[1:])
    if not variance > 0:
        bartlett = 1 - np.arange(1, len(autocovariances)) / horizon
        variance = autocovariances[0] + 2 * bartlett @ autocovariances[1:]
    if not variance > 0:
        return np.nan
    correction = np.sqrt((n + 1 - 2 * horizon + horizon * (horizon - 1) / n) / n)
    statistic = correction * improvement.mean() / np.sqrt(variance / n)
    return float(2 * t.sf(abs(statistic), n - 1))


def superior_predictive_ability(results: pd.DataFrame, horizon: int) -> float:
    """Hansen's (2005) SPA test: the p-value for "no model is more accurate than naive".

    It looks at the best result across all the models together, so trying more models doesn't make a lucky win
    more likely to pass. A stationary bootstrap keeps runs of neighbouring days together; its mean block is at
    least h days, the length over which overlapping forecasts stay correlated. Uses the `consistent` p-value.
    """
    losses = results.pivot(index="origin", columns="model", values="error")
    rivals = losses.drop(columns="naive")
    # A model that matched naive on every day can't beat it, and its zero-variance differences would break the
    # studentized statistic (arch reports p = 0 for them).
    rivals = rivals.loc[:, rivals.sub(losses.naive, axis=0).std() > 0]
    if rivals.empty:
        return np.nan
    block = max(horizon, int(np.sqrt(len(losses))))
    test = SPA(losses.naive, rivals, block_size=block, reps=1000, seed=0)
    test.compute()
    return float(test.pvalues["consistent"])


def by_trend(results: pd.DataFrame) -> pd.DataFrame:
    """Each model's error relative to naive, separately for origins in an uptrend and in a downtrend.

    The trend is known on the forecast date (the close against its 200-day average), so this asks whether an edge
    held in both kinds of market, not whether a model guessed the direction that then happened.
    """
    error = results.pivot_table(index="uptrend", columns="model", values="error", aggfunc="mean")
    return error.div(error.naive, axis=0).reindex([True, False])


def verdict(board: pd.DataFrame, results: pd.DataFrame, horizon: int) -> Verdict:
    best = board[board.model != "naive"].iloc[0]
    beaten = bool(best.relative_mae < 1)
    p_value = superior_predictive_ability(results, horizon)
    rows = results[results.model == best.model]
    moved = np.sign(rows.predicted - rows["last"])
    rises, falls = int((moved > 0).sum()), int((moved < 0).sum())
    one_way = "rise" if rises >= ONE_WAY * len(rows) else "fall" if falls >= ONE_WAY * len(rows) else None
    return Verdict(
        beaten=beaten,
        significant=bool(beaten and p_value < SIGNIFICANCE),
        best=best.model,
        relative_change=float(best.relative_mae - 1),
        origins=len(rows),
        up_origins=int((rows.actual > rows["last"]).sum()),
        period_change=float(rows.actual.iloc[-1] / rows["last"].iloc[0] - 1),
        p_value=p_value,
        one_way=one_way,
        one_way_origins=max(rises, falls) if one_way else 0,
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
    count = f"{result.one_way_origins:,} of {result.origins:,}"
    if result.one_way_origins == result.origins:
        count = f"all {result.origins:,}"
    moved = "rose" if result.period_change > 0 else "fell"
    return (
        f"It predicted a {result.one_way} on {count} days while the price {moved} {abs(result.period_change):.0%},"
        " so the win says more about this period's trend than about forecasting skill."
    )


@dataclass(frozen=True)
class Report:
    prices: pd.Series
    horizon: int
    results: pd.DataFrame
    board: pd.DataFrame
    trend: pd.DataFrame
    verdict: Verdict
    future_dates: pd.DatetimeIndex
    future: dict[str, Forecast]


def run_backtest(prices: pd.Series, horizon: int, models: list[Model] = MODELS) -> Report:
    results = walk_forward(prices, horizon, models)
    board = leaderboard(results, horizon)
    future_dates, future = future_paths(prices, horizon, models)
    return Report(
        prices, horizon, results, board, by_trend(results), verdict(board, results, horizon), future_dates, future
    )


def future_paths(prices: pd.Series, horizon: int, models: list[Model] = MODELS) -> tuple[pd.DatetimeIndex, dict]:
    """Each model's forecast from the latest price, dated by the symbol's trading calendar when it is known."""
    y = prices.to_numpy(dtype=float)
    dates = trading_days_after(prices.index[-1], horizon, prices.attrs.get("calendar"))
    return dates, {model.key: model.forecast(y, horizon) for model in models}
