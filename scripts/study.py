"""The study: every model against naive on every S&P 500 member, and on placebos of each stock, at every horizon.

    uv run python scripts/study.py universe                      # save today's member list, once
    uv run python scripts/study.py run --end 2026-10-23          # about a day; stop it and rerun to carry on
    uv run python scripts/study.py run --end 2026-10-23 --limit 10
    uv run python scripts/study.py export                        # write the dataset the Findings page reads

Every stock is cut off at the same close (`--end`). Prices are saved to the work folder the first time they load,
so a resumed run and the placebo use exactly the same data. A stock that can't be tested, say because it listed too
recently, gets a row saying why. A download that fails is retried on the next run instead.
"""

import argparse
import io
import json
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

import pandas as pd

from backcast.data import YEARS, DataError, load_prices
from backcast.evaluate import HORIZONS, ORIGINS, REFIT, run_backtest
from backcast.models import MODELS
from backcast.study import SEEDS, placebo, placebo_signs, rows
from backcast.version import code_version

OUT = Path(__file__).resolve().parents[1] / "src/backcast/web/static/backcast/study"
WIKIPEDIA = (
    "https://en.wikipedia.org/w/api.php?action=parse&page=List_of_S%26P_500_companies&prop=text|revid"
    "&format=json&formatversion=2"
)
USER_AGENT = "Backcast study (https://github.com/adeelbandesha/backcast)"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--work", type=Path, default=Path("study-work"), help="saved prices and progress")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("universe", help="save the current S&P 500 members from Wikipedia")
    run = commands.add_parser("run", help="backtest every member and its placebo")
    run.add_argument("--end", type=pd.Timestamp, required=True, help="the last close every stock uses")
    run.add_argument("--limit", type=int, help="only the first N members, for a trial")
    run.add_argument("--horizons", type=int, nargs="+", choices=HORIZONS, default=list(HORIZONS))
    commands.add_parser("export", help="write the dataset and its notes for the Findings page")
    args = parser.parse_args()
    {"universe": universe, "run": run_study, "export": export}[args.command](args)


def universe(args) -> None:
    request = urllib.request.Request(WIKIPEDIA, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        page = json.load(response)["parse"]
    table = next(t for t in pd.read_html(io.StringIO(page["text"])) if "Symbol" in t.columns)
    # Yahoo writes share classes with a dash: BRK.B is BRK-B.
    members = pd.DataFrame({"symbol": table.Symbol.str.replace(".", "-", regex=False), "name": table.Security})
    OUT.mkdir(parents=True, exist_ok=True)
    members.sort_values("symbol").to_csv(OUT / "universe.csv", index=False)
    source = f"https://en.wikipedia.org/w/index.php?title=List_of_S%26P_500_companies&oldid={page['revid']}"
    _write_notes({"universe": {"source": source, "retrieved": date.today().isoformat(), "members": len(members)}})
    print(f"{len(members)} members saved from Wikipedia revision {page['revid']}.")


def run_study(args) -> None:
    end = args.end.normalize()
    args.work.mkdir(parents=True, exist_ok=True)
    settings = {"end": f"{end:%Y-%m-%d}", "seeds": list(SEEDS)}
    saved = args.work / "settings.json"
    if saved.exists() and json.loads(saved.read_text()) != settings:
        sys.exit(f"{args.work} holds a study with {saved.read_text().strip()}. Use another --work folder.")
    saved.write_text(json.dumps(settings))

    progress = args.work / "results.jsonl"
    done = {(row["symbol"], row["horizon"], row.get("seed")) for row in _read(progress)}
    members = pd.read_csv(OUT / "universe.csv").symbol.tolist()[: args.limit]
    signs = {seed: placebo_signs(end - pd.DateOffset(years=YEARS + 1), end, seed) for seed in SEEDS}
    with progress.open("a") as log:
        for number, symbol in enumerate(members, 1):
            todo = [(seed, h) for seed in (None, *SEEDS) for h in args.horizons if (symbol, h, seed) not in done]
            if not todo:
                continue
            try:
                prices = _prices(args.work / "prices", symbol, end)
            except DataError as error:
                print(f"[{number}/{len(members)}] {symbol}: not loaded, will retry next run. {error}")
                continue
            for seed, horizon in todo:
                kind = "real" if seed is None else "placebo"
                started = time.monotonic()
                try:
                    if prices.index[-1] != end:
                        raise DataError(f"No close on {end:%d %b %Y}; the last is {prices.index[-1]:%d %b %Y}.")
                    data = prices if seed is None else placebo(prices, signs[seed])
                    lines = rows(run_backtest(data, horizon), kind, seed)
                except (DataError, ValueError) as error:
                    lines = [{"symbol": symbol, "horizon": horizon, "kind": kind, "seed": seed, "error": str(error)}]
                for line in lines:
                    log.write(json.dumps({**line, "code": code_version()}) + "\n")
                log.flush()
                outcome = lines[0].get("error") or f"SPA p = {lines[0]['spa_p_value']:.3f}"
                took = time.monotonic() - started
                label = kind if seed is None else f"{kind} {seed}"
                print(f"[{number}/{len(members)}] {symbol} {label} {horizon}d, {took:.0f} s: {outcome}")


def export(args) -> None:
    frame = pd.DataFrame(_read(args.work / "results.jsonl"))
    frame["error"] = frame.get("error")
    frame["seed"] = frame.seed.astype("Int64")  # real rows have none
    if (codes := frame.code.unique()).size > 1:
        sys.exit(f"The rows come from {codes.size} versions of the code ({', '.join(codes)}). Rerun in a new folder.")
    settings = json.loads((args.work / "settings.json").read_text())
    members = pd.read_csv(OUT / "universe.csv")
    failed = frame[frame.error.notna()]
    results = frame[frame.error.isna()].drop(columns=["error", "code"]).merge(members, on="symbol")
    results.round(6).to_csv(OUT / "results.csv", index=False)
    excluded = failed[failed.kind == "real"].drop_duplicates("symbol")[["symbol", "error"]]
    missing = sorted(set(members.symbol) - set(frame.symbol))
    _write_notes(
        {
            "study": {
                **settings,
                "code": codes[0],
                "years": YEARS,
                "origins": ORIGINS,
                "refit": REFIT,
                "models": [model.key for model in MODELS],
                "tested": int(results[results.kind == "real"].symbol.nunique()),
                "excluded": excluded.rename(columns={"error": "reason"}).to_dict("records"),
                "not_loaded": missing,
            }
        }
    )
    print(f"{len(results)} rows written; {len(excluded)} stocks excluded, {len(missing)} never loaded.")


def _prices(folder: Path, symbol: str, end: pd.Timestamp) -> pd.Series:
    folder.mkdir(exist_ok=True)
    path = folder / f"{symbol}.pkl"
    if path.exists() and (saved := pd.read_pickle(path)).index[-1] >= end:
        return saved.loc[:end]
    prices = load_prices(symbol)
    prices.to_pickle(path)
    return prices.loc[:end]


def _read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _write_notes(update: dict) -> None:
    path = OUT / "study.json"
    notes = json.loads(path.read_text()) if path.exists() else {}
    path.write_text(json.dumps({**notes, **update}, indent=2) + "\n")


if __name__ == "__main__":
    main()
