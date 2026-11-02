"""Across the S&P 500 study's stocks, how often does a volatility model beat naive, and the long-run average?

Runs the volatility backtest on every stock the study saved prices for, cut off at the study's end date, at every
horizon. It does the same on a control: each stock's daily returns in shuffled order, which keeps every move's size
but breaks up the calm and volatile stretches. A model that wins on the control isn't using volatility clustering,
only averaging more days than naive does. The Method page quotes its output.

    uv run python scripts/volatility_check.py --work ../backcast-study/study-work   # about an hour on four cores
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from backcast import volatility
from backcast.evaluate import HORIZONS, SIGNIFICANCE

SEED = 20261102


def shuffled(prices: pd.Series, seed: int) -> pd.Series:
    returns = np.random.default_rng(seed).permutation(np.diff(np.log(prices.to_numpy())))
    path = prices.iloc[0] * np.exp(np.concatenate([[0], np.cumsum(returns)]))
    return pd.Series(path, index=prices.index, name=prices.name)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--work", type=Path, required=True, help="the study's work folder")
    parser.add_argument("--out", type=Path, default=Path("volatility-check.csv"))
    args = parser.parse_args()

    end = pd.Timestamp(json.loads((args.work / "settings.json").read_text())["end"])
    rows = []
    for path in sorted((args.work / "prices").glob("*.pkl")):
        prices = pd.read_pickle(path).loc[:end]
        for control in (False, True):
            data = shuffled(prices, SEED) if control else prices
            for horizon in HORIZONS:
                try:
                    result = volatility.run_backtest(data, horizon).verdict
                except ValueError as error:  # too little history
                    print(f"{prices.name}: {error}", flush=True)
                    break
                rows.append(
                    {
                        "symbol": prices.name,
                        "horizon": horizon,
                        "control": control,
                        "best": result.best,
                        "beats_naive": result.significant,
                        "beats_long_run": result.long_run_p_value < SIGNIFICANCE,
                        "naive_p_value": result.p_value,
                        "long_run_p_value": result.long_run_p_value,
                    }
                )
        print(f"{prices.name} done", flush=True)

    frame = pd.DataFrame(rows)
    frame.to_csv(args.out, index=False)
    print(f"\n{frame.symbol.nunique()} stocks. Share where some model beat each baseline (SPA p < {SIGNIFICANCE}):\n")
    summary = frame.groupby(["control", "horizon"])[["beats_naive", "beats_long_run"]].mean()
    print(summary.map(lambda share: f"{share:.1%}").to_string())
    print("\nBest model, real stocks:\n")
    print(pd.crosstab(frame[~frame.control].best, frame[~frame.control].horizon).to_string())


if __name__ == "__main__":
    main()
