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
from .models import LEVEL, MODELS, MODELS_BY_KEY, QUANTILES, Forecast, Model, Naive

HORIZONS = (1, 5, 20)
ORIGINS = 504  # about two years of trading days, one forecast origin per day
REFIT = 20  # trading days between re-estimates; scripts/refit_check.py tests the choice
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
    next one; `scripts/refit_check.py` compares this with refitting every day. Scores are on log prices, so a 2% miss
    counts the same at any price level.
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
    rows = [
        _row(model.key, model.name, y, prices.index, average, origin, horizon, predicted, quantiles)
        for origin, endpoints in zip(chain(*blocks), chain(*forecasts), strict=True)
        for model, (predicted, quantiles) in zip(models, endpoints, strict=True)
    ]
    return pd.DataFrame(rows)


def _row(key, name, y, dates, average, origin, horizon, predicted, quantiles) -> dict:
    """Score one forecast of `y[origin + horizon - 1]` made from `y[:origin]`. `quantiles` holds all of
    `QUANTILES`, only the outer pair (the 80% interval, so no CRPS), or nothing (a point forecast).
    """
    actual = np.log(y[origin + horizon - 1])
    with np.errstate(invalid="ignore", divide="ignore"):
        point, quantiles = np.log(predicted), np.log(np.asarray(quantiles, dtype=float))
    # Fail loudly: a NaN score would be skipped by the averages, and the models compared on different days.
    if not (np.isfinite(point) and np.isfinite(quantiles).all() and (np.diff(quantiles) >= 0).all()):
        raise ValueError(
            f"{name} gave an invalid forecast on {dates[origin - 1]:%d %b %Y}: the forecast and its quantiles must "
            "be positive prices, with the quantiles in increasing order."
        )
    interval = len(quantiles) > 0
    return {
        "model": key,
        "origin": dates[origin - 1],
        "target": dates[origin + horizon - 1],
        "last": y[origin - 1],
        "actual": y[origin + horizon - 1],
        "predicted": predicted,
        "lower": np.exp(quantiles[0]) if interval else np.nan,
        "upper": np.exp(quantiles[-1]) if interval else np.nan,
        "error": abs(actual - point),
        "crps": _crps(quantiles, actual) if len(quantiles) == len(QUANTILES) else np.nan,
        "interval": _interval_score(quantiles[0], quantiles[-1], actual) if interval else np.nan,
        "uptrend": y[origin - 1] > average[origin - 1],
    }


def required_prices(horizon: int) -> int:
    """Training history for the first origin, then enough origins for the significance tests to mean something."""
    return MIN_HISTORY + MIN_ORIGINS - 1 + horizon


def _crps(quantiles: np.ndarray, actual: float) -> float:
    """CRPS approximated by twice the mean pinball loss over the forecast quantiles. For a point forecast it equals
    the absolute error. The quantiles stop at 10% and 90%, so a miss far into the tails costs no more than a near
    one here; the interval score is the metric that charges for it.
    """
    return float(2 * np.mean(((actual < quantiles) - QUANTILES) * (quantiles - actual)))


def _interval_score(lower: float, upper: float, actual: float) -> float:
    """Gneiting and Raftery's interval score: the interval's width, plus 2/alpha times any distance by which the
    actual falls outside it. Lower is better; a narrow interval only pays off if it also holds the actual.
    """
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
    from each origin in the block. `known` ends at the block's last origin, so no model can see a later price.
    """
    forecasters = [model.fit(known[: origins[0]], horizon) for model in models]
    endpoints = []
    for origin in origins:
        forecasts = [forecaster(known[:origin]) for forecaster in forecasters]
        endpoints.append([_endpoint(f) for f in forecasts])
    return endpoints


def leaderboard(results: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """One row per model, best first. `relative_mae` below 1 means more accurate than the naive forecast."""
    naive = results[results.model == "naive"].set_index("origin")
    rows = []
    for key, group in results.groupby("model", sort=False):
        group = group.set_index("origin")
        improvement = naive.error - group.error
        tied = _tied(group.predicted, naive.predicted)
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
                "win_rate": ((improvement > 0) & ~tied).mean() if key != "naive" else np.nan,
                "tie_rate": tied.mean() if key != "naive" else np.nan,
                "p_value": np.nan if tied.all() else _diebold_mariano(improvement.to_numpy(), horizon),
                "direction": direction,
                "coverage": group.actual.between(group.lower, group.upper).where(group.lower.notna()).mean(),
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


def _tied(predicted, naive) -> np.ndarray:
    """Days a model forecast exactly what naive did, as ARIMA does when it settles on a random walk. The forecasts
    differ only by rounding (exp(log(p)) isn't always p), so their errors differ by noise that is neither a win
    nor a loss.
    """
    return np.isclose(predicted, naive, rtol=1e-9, atol=0)


def _diebold_mariano(improvement: np.ndarray, horizon: int) -> float:
    """Two-sided p-value for "the mean loss difference is zero" (Diebold and Mariano, 1995).

    Forecasts made on consecutive days share h - 1 days of their future, so their errors are correlated over h - 1
    lags. The variance adds those autocovariances with equal weights, as in the original test, with Harvey,
    Leybourne and Newbold's (1997) small-sample correction and a t distribution. At h = 1 this is exactly the paired
    t-test. Equal weights keep the false-positive rate near the nominal level on overlapping forecasts (see
    `test_diebold_mariano_holds_its_size_on_overlapping_forecasts`); Newey-West weights reject about twice as often
    and are only the fallback for a sample whose variance comes out negative.
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


def superior_predictive_ability(results: pd.DataFrame, horizon: int, benchmark: str = "naive") -> float:
    """Hansen's (2005) SPA test: the p-value for "no model is more accurate than the benchmark", naive by default.

    It looks at the best result across all the models together, so trying more models doesn't make a lucky win
    more likely to pass. A stationary bootstrap keeps runs of neighbouring days together; its mean block is at
    least h days, the length over which overlapping forecasts stay correlated. Uses the `consistent` p-value.
    """
    losses = results.pivot(index="origin", columns="model", values="error")
    forecasts = results.pivot(index="origin", columns="model", values="predicted")
    # A model that matched naive on every day can't beat it, and its differences, zero or rounding noise, would
    # break the studentized statistic (arch reports p = 0 for exact zeros).
    rivals = [key for key in losses if key != benchmark and not _tied(forecasts[key], forecasts[benchmark]).all()]
    rivals = losses[rivals]
    if rivals.empty:
        return np.nan
    block = max(horizon, int(np.sqrt(len(losses))))
    test = SPA(losses[benchmark], rivals, block_size=block, reps=1000, seed=0)
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
    """The verdict in one sentence: did the best model beat naive, and is the margin bigger than noise."""
    name = MODELS_BY_KEY[result.best].name if result.best in MODELS_BY_KEY else result.best  # scored forecasts
    margin = abs(result.relative_change)
    amount = f"{margin:.1%}" if margin >= 0.0005 else "less than 0.1%"
    if result.significant:
        return f"{name} beat the naive forecast by {amount}, a statistically significant margin."
    if result.beaten:
        return f"{name} edged out the naive forecast by {amount}, but the margin is within the noise."
    if np.isclose(result.relative_change, 0):
        return "Nothing beat the naive forecast. The best any model managed was to match it."
    if result.best not in MODELS_BY_KEY:  # scored forecasts are the only rival, so there's no "closest"
        return f"{name} didn't beat the naive forecast: the error was {amount} higher."
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
    """A backtest's output. `board` is the leaderboard, one row per model; `verdict` sums it up (see `headline`);
    `results` has one row per model and forecast date; `trend` splits the board into up and down markets; `future`
    is each model's forecast from the last price, for `future_dates`.
    """

    prices: pd.Series
    horizon: int
    results: pd.DataFrame
    board: pd.DataFrame
    trend: pd.DataFrame
    verdict: Verdict
    future_dates: pd.DatetimeIndex
    future: dict[str, Forecast]


def run_backtest(prices: pd.Series, horizon: int, models: list[Model] = MODELS) -> Report:
    """Backtest every model on `prices` (from `load_prices`) at `horizon` trading days, and test it against naive."""
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


def score(forecasts: pd.DataFrame, prices: pd.Series, horizon: int, name: str = "Your forecasts") -> Report:
    """Score forecasts made elsewhere against naive, with the same metrics and tests as a backtest.

    `forecasts` has one row per forecast: `date`, the trading day whose close it was made after, and `forecast`,
    the price it predicts `horizon` trading days later, scored as the median. Optional `q10` to `q90` columns are
    its quantiles: `q10` and `q90` alone give the 80% interval, all nine add CRPS. Forecasts whose target is after
    the last price are left out until it arrives. The dates can be checked; that no later price was used can't be.
    """
    if name == "naive":
        raise ValueError('"naive" is the benchmark\'s name; give your forecasts another.')
    missing = {"date", "forecast"} - set(forecasts.columns)
    if missing:
        raise ValueError(f"The forecasts need a {' and a '.join(sorted(missing))} column.")
    dates = pd.DatetimeIndex(pd.to_datetime(forecasts["date"]))
    dates = (dates.tz_localize(None) if dates.tz else dates).normalize()
    if (repeated := dates[dates.duplicated()]).size:
        raise ValueError(f"There is more than one forecast from {repeated[0]:%d %b %Y}.")
    positions = prices.index.get_indexer(dates)
    if (positions < 0).any():
        raise ValueError(f"{dates[positions < 0][0]:%d %b %Y} isn't a trading day in the prices.")
    if positions.min() < TREND_DAYS - 1:
        raise ValueError(f"The prices need to start at least {TREND_DAYS} trading days before the first forecast.")

    levels = [f"q{round(q * 100)}" for q in QUANTILES]
    if set(levels) <= set(forecasts.columns):
        columns = levels
    elif {"q10", "q90"} <= set(forecasts.columns):
        columns = ["q10", "q90"]
    else:
        columns = []
    y = prices.to_numpy(dtype=float)
    average = prices.rolling(TREND_DAYS).mean().to_numpy()
    naive = Naive()
    rows = []
    for position, predicted, quantiles in zip(
        positions, forecasts["forecast"].to_numpy(dtype=float), forecasts[columns].to_numpy(dtype=float), strict=True
    ):
        origin = position + 1  # the number of prices known when the forecast was made
        if origin + horizon > len(y):
            continue
        benchmark = naive.forecast(y[:origin], horizon)
        rows.append(_row("naive", naive.name, y, prices.index, average, origin, horizon, *_endpoint(benchmark)))
        rows.append(_row(name, name, y, prices.index, average, origin, horizon, predicted, quantiles))
    if len(rows) // 2 < MIN_ORIGINS:
        raise ValueError(
            f"Scoring needs at least {MIN_ORIGINS} forecasts whose target date has passed, got {len(rows) // 2}."
        )

    results = pd.DataFrame(rows).sort_values(["origin", "model"], kind="stable", ignore_index=True)
    board = leaderboard(results, horizon)
    no_future = pd.DatetimeIndex([])
    return Report(prices, horizon, results, board, by_trend(results), verdict(board, results, horizon), no_future, {})


def _endpoint(forecast: Forecast) -> tuple[float, np.ndarray]:
    return float(forecast.median[-1]), forecast.quantiles[:, -1]
