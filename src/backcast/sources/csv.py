"""Prices from CSV files: one file per symbol, such as AAPL.csv, in the folder named by BACKCAST_CSV_DIR."""

import os
from pathlib import Path

import pandas as pd

from ..data import DataError, SourceError

ATTRIBUTION = "Daily closes from local CSV files."


def load_prices(symbol: str, years: int) -> pd.Series:
    path = _files().get(symbol)
    if path is None:
        raise DataError(f"There is no price file for {symbol}.")
    try:
        table = pd.read_csv(path)
    except OSError as exc:
        raise DataError(f"Couldn't open the price file for {symbol}.") from exc
    except ValueError as exc:
        raise DataError(f"{path.name} isn't a readable CSV file: {exc}") from exc
    columns = {str(name).strip().lower(): name for name in table.columns}
    if "date" not in columns or "close" not in columns:
        raise DataError(f"{path.name} needs a Date column and a Close column.")
    try:
        dates = pd.DatetimeIndex(pd.to_datetime(table[columns["date"]]))
    except (TypeError, ValueError) as exc:
        raise DataError(f"{path.name} has a date that can't be read: {exc}") from exc
    close = pd.Series(table[columns["close"]].to_numpy(), index=dates, name=symbol)
    close.attrs["calendar"] = os.environ.get("BACKCAST_CSV_CALENDAR", "").strip() or None
    return close


def search_symbols(query: str, limit: int) -> list[dict]:
    query = query.strip().upper()
    files = _files()
    matches = sorted((symbol for symbol in files if query in symbol), key=lambda s: (not s.startswith(query), s))
    return [{"symbol": symbol, "name": files[symbol].name, "exchange": "CSV"} for symbol in matches[:limit]]


def _files() -> dict[str, Path]:
    folder = os.environ.get("BACKCAST_CSV_DIR", "").strip()
    if not folder or not Path(folder).is_dir():
        raise SourceError("The csv price source needs BACKCAST_CSV_DIR set to a folder of price files.")
    # Matched by listing the folder, so a symbol never becomes part of a path.
    return {path.stem.upper(): path for path in Path(folder).iterdir() if path.suffix.lower() == ".csv"}
