import argparse
import os
import subprocess
import sys

from .data import DataError, SourceError, load_prices, normalize_symbol
from .evaluate import HORIZONS, headline, run_backtest, trend_note
from .models import MODELS_BY_KEY


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="backcast", description="Walk-forward benchmarks for stock forecasting models."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="backtest every model on one ticker")
    run.add_argument("symbol", help="ticker symbol, e.g. AAPL")
    run.add_argument("--horizon", type=int, choices=HORIZONS, default=5, help="trading days ahead (default: 5)")

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
        report = run_backtest(prices, args.horizon)
    except (DataError, SourceError, ValueError) as error:
        print(f"backcast: {error}", file=sys.stderr)
        return 1

    board = report.board.assign(model=report.board.model.map(lambda key: MODELS_BY_KEY[key].name))
    board = board.assign(mae=board.mae * 100).rename(columns={"mae": "mae_pct"})  # log error, read as a percentage
    summary = f"{headline(report.verdict)} {trend_note(report.verdict)}".strip()
    verdict = report.verdict
    print(f"\n{prices.name}: {args.horizon}-day forecasts from {verdict.origins} trading days\n")
    print(f"{summary}\nSPA p-value that no model beats naive: {verdict.p_value:.3f}\n")
    print(board.to_string(index=False, float_format=lambda value: f"{value:.3f}", na_rep="—"), end="\n\n")
    return 0


def _django(*command: str) -> None:
    from django.core.management import execute_from_command_line

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backcast.web.settings")
    execute_from_command_line(["backcast", *command])
