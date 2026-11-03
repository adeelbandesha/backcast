import argparse
import os
import subprocess
import sys

import pandas as pd

from . import volatility
from .data import DataError, SourceError, load_prices, normalize_symbol
from .evaluate import HORIZONS, run_backtest, score


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="backcast",
        description="Walk-forward benchmarks for stock forecasting models.",
        epilog="Docs: https://github.com/adeelbandesha/backcast#readme",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="backtest every model on one ticker")
    run.add_argument("symbol", help="ticker symbol, e.g. AAPL")
    run.add_argument("--horizon", type=int, choices=HORIZONS, default=5, help="trading days ahead (default: 5)")

    vol = commands.add_parser("volatility", help="backtest the volatility models on one ticker")
    vol.add_argument("symbol", help="ticker symbol, e.g. AAPL")
    vol.add_argument("--horizon", type=int, choices=HORIZONS, default=5, help="trading days ahead (default: 5)")

    scored = commands.add_parser("score", help="score your own forecasts from a CSV file against naive")
    scored.add_argument("file", help="CSV with columns date and forecast, and optionally q10 to q90")
    scored.add_argument("symbol", help="the ticker the forecasts are for")
    scored.add_argument("--horizon", type=int, choices=HORIZONS, default=5, help="trading days ahead (default: 5)")

    web = commands.add_parser("web", help="start the web app")
    web.add_argument("address", nargs="?", default="127.0.0.1:8000", help="host:port (default: 127.0.0.1:8000)")
    web.add_argument("--no-worker", action="store_true", help="don't start a worker; run `backcast worker` yourself")

    commands.add_parser("worker", help="run queued backtests and the nightly examples for the web app")

    args = parser.parse_args(argv)
    if args.command == "worker":
        _django("migrate", "--verbosity", "0")
        _django("run_huey")
        return 0
    if args.command == "web":
        worker = None
        # Django's autoreloader runs this command again in a child process, which must not repeat the setup.
        if os.environ.get("RUN_MAIN") != "true":
            _django("migrate", "--verbosity", "0")
            if not args.no_worker:
                worker = subprocess.Popen([sys.executable, "-m", "backcast", "worker"])
        try:
            _django("runserver", args.address)
        finally:
            if worker:
                worker.terminate()
        return 0

    try:
        prices = load_prices(normalize_symbol(args.symbol))
        if args.command == "score":
            report = score(pd.read_csv(args.file, encoding="utf-8-sig"), prices, args.horizon)
        else:
            report = (volatility.run_backtest if args.command == "volatility" else run_backtest)(prices, args.horizon)
    except (DataError, SourceError, ValueError, OSError) as error:  # OSError: the CSV file can't be read
        print(f"backcast: {error}", file=sys.stderr)
        return 1
    if args.command == "volatility":
        _print_volatility(report)
        return 0

    board = report.table()
    verdict = report.verdict
    if args.command == "score":
        print(f"\n{prices.name}: {verdict.origins} of your {args.horizon}-day forecasts, scored against naive\n")
    else:
        print(f"\n{prices.name}: {args.horizon}-day forecasts from {verdict.origins} trading days\n")
    print("\n".join(report.summary()), end="\n\n")
    print(board.to_string(index=False, float_format=lambda value: f"{value:.3f}", na_rep="—"), end="\n\n")
    return 0


def _print_volatility(report) -> None:
    origins = report.verdict.origins
    print(f"\n{report.prices.name}: {report.horizon}-day volatility forecasts from {origins} trading days\n")
    print("\n".join(report.summary()), end="\n\n")
    print(report.table().to_string(index=False, float_format=lambda value: f"{value:.3f}", na_rep="—"), end="\n\n")


def _django(*command: str) -> None:
    from django.core.management import execute_from_command_line

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backcast.web.settings")
    execute_from_command_line(["backcast", *command])
