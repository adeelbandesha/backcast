"""The study: every model against naive on a fixed list of stocks, each one next to a placebo.

The placebo replays a stock's own daily moves with random signs. The size of every move, and so the volatility, its
clustering and the fat tails, stay as they were, but the direction becomes a coin toss: no model can beat naive on
it except by luck. How often the tests find a winner on the placebos is what chance alone produces on these data.
One sign per date is shared by every stock, so stocks that moved together still move together.
"""

import numpy as np
import pandas as pd

from .evaluate import Report

SEED = 20261024


def placebo_signs(start, end, seed: int = SEED) -> pd.Series:
    dates = pd.bdate_range(start, end)
    return pd.Series(np.random.default_rng(seed).choice([-1.0, 1.0], len(dates)), index=dates)


def placebo(prices: pd.Series, signs: pd.Series) -> pd.Series:
    moves = np.log(prices).diff().iloc[1:]
    flips = signs.reindex(moves.index)
    if flips.isna().any():
        raise ValueError(f"No placebo sign for {flips.index[flips.isna()][0]:%d %b %Y}.")
    path = prices.iloc[0] * np.exp((moves * flips).cumsum())
    replayed = pd.concat([prices.iloc[:1], path]).rename(prices.name)
    replayed.attrs = dict(prices.attrs)
    return replayed


def rows(report: Report, kind: str) -> list[dict]:
    """One row per model, each carrying the stock's verdict, for a flat dataset."""
    verdict = report.verdict
    common = {
        "symbol": report.prices.name,
        "horizon": report.horizon,
        "kind": kind,
        "spa_p_value": verdict.p_value,
        "significant": verdict.significant,
        "best": verdict.best,
        "origins": verdict.origins,
        "first_origin": f"{report.results.origin.min():%Y-%m-%d}",
        "last_target": f"{report.results.target.max():%Y-%m-%d}",
        "period_change": verdict.period_change,
        "up_origins": verdict.up_origins,
    }
    return [{key: _plain(value) for key, value in {**common, **row}.items()} for row in report.board.to_dict("records")]


def _plain(value):
    # JSON lines can't hold NumPy scalars, and NaN becomes null.
    if isinstance(value, np.generic):
        value = value.item()
    return None if isinstance(value, float) and np.isnan(value) else value
