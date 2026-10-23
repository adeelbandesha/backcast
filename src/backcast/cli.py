import argparse
import os
import sys

from .data import DataError, load_prices, normalize_symbol
from .evaluate import HORIZONS, WINDOWS, headline, run_backtest, trend_note
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

    args = parser.parse_args(argv)
    if args.command == "web":
        from django.core.management import execute_from_command_line

        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backcast.web.settings")
        execute_from_command_line(["backcast", "runserver", args.address])
        return 0

    try:
        prices = load_prices(normalize_symbol(args.symbol))
        report = run_backtest(prices, args.horizon)
    except (DataError, ValueError) as error:
        print(f"backcast: {error}", file=sys.stderr)
        return 1

    board = report.board.assign(model=report.board.model.map(lambda key: MODELS_BY_KEY[key].name))
    summary = f"{headline(report.verdict)} {trend_note(report.verdict)}".strip()
    print(f"\n{prices.name}: {args.horizon}-day forecasts over {WINDOWS} windows\n")
    print(f"{summary}\n")
    print(board.to_string(index=False, float_format=lambda value: f"{value:.3f}", na_rep="—"), end="\n\n")
    return 0
