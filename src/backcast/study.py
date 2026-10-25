"""The S&P 500 study: every model against naive on each member, and on placebos of it.

A placebo replays a stock's own daily moves with random signs, so a model that beats naive on one did it by luck.
The Findings page explains the design.
"""

import numpy as np
import pandas as pd

from .evaluate import SIGNIFICANCE, Report

SEED = 20261024
SEEDS = tuple(SEED + draw for draw in range(5))


def placebo_signs(start, end, seed: int = SEED) -> pd.Series:
    """One random sign per weekday, shared by every stock, so stocks that moved together still do."""
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


def rows(report: Report, kind: str, seed: int | None = None) -> list[dict]:
    """One row per model, each carrying the stock's verdict, for a flat dataset."""
    verdict = report.verdict
    common = {
        "symbol": report.prices.name,
        "horizon": report.horizon,
        "kind": kind,
        "seed": seed,
        "spa_p_value": verdict.p_value,
        "significant": verdict.significant,
        "best": verdict.best,
        "origins": verdict.origins,
        "first_origin": f"{report.results.origin.min():%Y-%m-%d}",
        "last_target": f"{report.results.target.max():%Y-%m-%d}",
        "period_change": verdict.period_change,
        "up_origins": verdict.up_origins,
        "one_way": verdict.one_way,
        "one_way_origins": verdict.one_way_origins,
    }
    lines = []
    for row in report.board.to_dict("records"):
        trend = {
            "relative_mae_up": report.trend.loc[True, row["model"]],
            "relative_mae_down": report.trend.loc[False, row["model"]],
        }
        lines.append({key: _plain(value) for key, value in {**common, **row, **trend}.items()})
    return lines


def _plain(value):
    # JSON lines can't hold NumPy scalars, and NaN becomes null.
    if isinstance(value, np.generic):
        value = value.item()
    return None if isinstance(value, float) and np.isnan(value) else value


def findings(results: pd.DataFrame) -> dict:
    """The Findings page's numbers, from the exported dataset.

    The dataset has one row per stock, horizon, real or placebo draw, and model. Placebo figures are averaged over
    the draws, with the lowest and highest draw beside them.
    """
    # A trend bet: the best model forecast one direction from nearly every origin, and the price went that way.
    bet = results.one_way.map({"rise": 1, "fall": -1})
    results = results.assign(trend_bet=results.significant & (bet == np.sign(results.period_change)))
    stocks = results.drop_duplicates(["symbol", "horizon", "seed"])
    models = results[results.model != "naive"].assign(
        won=lambda frame: (frame.relative_mae < 1) & (frame.p_adjusted < SIGNIFICANCE)
    )
    horizons = sorted(stocks.horizon.unique().tolist())
    summary = []
    for horizon in horizons:
        real = stocks[(stocks.horizon == horizon) & (stocks.kind == "real")]
        shares = stocks[(stocks.horizon == horizon) & (stocks.kind == "placebo")].groupby("seed").significant.mean()
        summary.append(
            {
                "horizon": horizon,
                "real": {
                    "tested": len(real),
                    "significant": int(real.significant.sum()),
                    "share": real.significant.mean(),
                    "trend_bets": int(real.trend_bet.sum()),
                },
                # A partial export can hold a horizon's real rows before any of its placebos.
                "placebo": {"draws": len(shares), "share": shares.mean(), "low": shares.min(), "high": shares.max()},
            }
        )
    won = models.pivot_table(index="model", columns=["horizon", "kind"], values="won", aggfunc="mean").reindex(
        columns=pd.MultiIndex.from_product([horizons, ["real", "placebo"]])
    )
    winners = results[(results.kind == "real") & results.significant & (results.model == results.best)]
    return {
        "horizons": horizons,
        "summary": summary,
        "models": {
            key: [{"real": row[h, "real"], "placebo": row[h, "placebo"]} for h in horizons]
            for key, row in won.iterrows()
        },
        "p_values": {
            int(h): {kind: group.spa_p_value.dropna().tolist() for kind, group in by_horizon.groupby("kind")}
            for h, by_horizon in stocks.groupby("horizon")
        },
        "winners": winners.sort_values(["horizon", "spa_p_value", "symbol"])[
            [
                "symbol",
                "name",
                "horizon",
                "best",
                "relative_mae",
                "relative_mae_up",
                "relative_mae_down",
                "spa_p_value",
                "period_change",
                "trend_bet",
            ]
        ].to_dict("records"),
    }
