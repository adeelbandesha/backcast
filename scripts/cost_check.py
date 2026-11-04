"""Is the one-day edge worth trading? Trades each model's one-day forecast on every study stock, after costs.

At each close a model's position is long when it forecasts a rise, short when it forecasts a fall and flat when it
forecasts no change, and it earns the next day's price return. Every change of position costs the cost per unit of
stock traded, so turning from long to short pays it twice. The break-even cost is the cost that leaves the strategy
with no return at all. It does the same on the study's first placebo draw, where the random signs leave no reversal
to trade. The Findings page quotes its output.

It is kind to the strategy: it trades at the close its forecast was made from, with no borrowing fee for shorts and
no dividends either way (the prices aren't adjusted for them).

    uv run python scripts/cost_check.py --work ../backcast-study/study-work   # about three hours on eight cores

Stopping it and running the same command again carries on where it stopped.
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from backcast.data import YEARS
from backcast.evaluate import walk_forward
from backcast.models import MODELS
from backcast.study import SEED, placebo, placebo_signs
from backcast.volatility import TRADING_DAYS

COSTS = (1, 5)  # basis points per unit of stock traded
STUDY = Path(__file__).resolve().parents[1] / "src/backcast/web/static/backcast/study/study.json"


def trading(results: pd.DataFrame) -> pd.DataFrame:
    """One row per model from a one-day `walk_forward`: yearly return before and after costs, units traded a day,
    and the break-even cost in basis points. Naive never forecasts a move, so it never trades and is left out.
    """
    rows = []
    for key, group in results.groupby("model", sort=False):
        position = np.sign(group.predicted - group["last"]).to_numpy()
        traded = np.abs(np.diff(position, prepend=0))
        if not traded.any():
            continue
        gross = position * (group.actual / group["last"] - 1).to_numpy()
        row = {"model": key, "gross": gross.mean() * TRADING_DAYS, "turnover": traded.mean()}
        row["break_even"] = gross.mean() / traded.mean() * 1e4
        for cost in COSTS:
            row[f"net_{cost}"] = (gross - cost / 1e4 * traded).mean() * TRADING_DAYS
        rows.append(row)
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--work", type=Path, required=True, help="the study's work folder")
    parser.add_argument("--out", type=Path, default=Path("cost-check.jsonl"))
    args = parser.parse_args()

    end = pd.Timestamp(json.loads((args.work / "settings.json").read_text())["end"])
    studied = json.loads(STUDY.read_text())["study"]["models"]
    models = [model for model in MODELS if model.key in studied]  # an installed Chronos wasn't in the study
    signs = placebo_signs(end - pd.DateOffset(years=YEARS + 1), end, SEED)
    done = {json.loads(line)["symbol"] for line in args.out.read_text().splitlines()} if args.out.exists() else set()
    with args.out.open("a") as log:
        for path in sorted((args.work / "prices").glob("*.pkl")):
            prices = pd.read_pickle(path).loc[:end]
            if prices.name in done:
                continue
            try:
                runs = {False: walk_forward(prices, 1, models), True: walk_forward(placebo(prices, signs), 1, models)}
            except ValueError as error:  # too little history
                print(f"{prices.name}: {error}", flush=True)
                continue
            for control, results in runs.items():
                hold = (results.actual / results["last"] - 1).groupby(results.origin).first().mean() * TRADING_DAYS
                for row in trading(results).to_dict("records"):
                    log.write(json.dumps({"symbol": prices.name, "control": control, "hold": hold, **row}) + "\n")
            log.flush()
            print(f"{prices.name} done", flush=True)

    frame = pd.read_json(args.out, lines=True)
    print(f"\n{frame.symbol.nunique()} stocks. Medians across stocks (returns a year; costs in basis points):\n")
    columns = ["break_even", "turnover", "gross", *(f"net_{cost}" for cost in COSTS), "hold"]
    summary = frame.groupby(["control", "model"])[columns].median()
    summary["profit_after_1"] = frame.groupby(["control", "model"]).net_1.apply(lambda net: (net > 0).mean())
    print(summary.round(4).to_string())


if __name__ == "__main__":
    main()
