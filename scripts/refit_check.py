"""How much does re-estimating the models less often change a backtest?

Runs the full backtest for each symbol and horizon with models re-estimated every day, every 5 days and every
REFIT days, then compares each schedule with the daily one: how far every model's error relative to naive moves,
whether the significance verdict changes, and how long it takes. The Method page quotes its output.

    uv run python scripts/refit_check.py               # AAPL, KO and NVDA; about half an hour on four cores
    uv run python scripts/refit_check.py SPY MSFT
"""

import argparse
import sys
import time

import pandas as pd

from backcast.data import load_prices
from backcast.evaluate import HORIZONS, REFIT, leaderboard, verdict, walk_forward

INTERVALS = sorted({1, 5, REFIT})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("symbols", nargs="*", default=["AAPL", "KO", "NVDA"])
    symbols = parser.parse_args().symbols

    runs, scores = [], []
    for symbol in symbols:
        prices = load_prices(symbol)
        print(f"{symbol}: closes to {prices.index[-1]:%d %b %Y}", file=sys.stderr)
        for horizon in HORIZONS:
            for refit in INTERVALS:
                start = time.perf_counter()
                results = walk_forward(prices, horizon, refit=refit)
                seconds = time.perf_counter() - start
                board = leaderboard(results, horizon)
                result = verdict(board, results, horizon)
                runs.append((symbol, horizon, refit, seconds, result.significant))
                scores += [(symbol, horizon, refit, row.model, row.relative_mae) for row in board.itertuples()]
                print(f"  h={horizon} refit={refit}: {seconds:.0f}s", file=sys.stderr)

    runs = pd.DataFrame(runs, columns=["symbol", "horizon", "refit", "seconds", "significant"])
    scores = pd.DataFrame(scores, columns=["symbol", "horizon", "refit", "model", "relative_mae"])
    scores = scores[scores.model != "naive"].pivot_table(
        index=["symbol", "horizon", "model"], columns="refit", values="relative_mae"
    )
    verdicts = runs.pivot_table(index=["symbol", "horizon"], columns="refit", values="significant")
    seconds = runs.groupby("refit").seconds.sum()

    horizons = ", ".join(map(str, HORIZONS))
    print(f"\nCompared with daily refits, over {len(verdicts)} backtests ({', '.join(symbols)}; {horizons}-day):\n")
    print("refit every   error vs naive moved by (mean / max)   verdicts changed   time")
    for refit in INTERVALS[1:]:
        shift = (scores[refit] - scores[1]).abs() * 100  # percentage points of relative error
        changed = int((verdicts[refit] != verdicts[1]).sum())
        print(
            f"{refit:>5} days    {shift.mean():>14.2f} / {shift.max():.2f} points"
            f"   {changed:>8} of {len(verdicts)}   1/{seconds[1] / seconds[refit]:.0f} of daily"
        )


if __name__ == "__main__":  # the backtest's worker pool re-imports this module
    main()
