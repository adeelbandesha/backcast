"""Daily prices and symbol search from the configured price source, plus trading calendars.

A price source is a module with `load_prices(symbol, years)` and, optionally, `search_symbols(query, limit)` and
`ATTRIBUTION`. `BACKCAST_PRICE_SOURCE` picks one; the README describes the contract.
"""

import importlib
import os
import re
from functools import cache
from types import ModuleType

import exchange_calendars as xcals
import numpy as np
import pandas as pd

YEARS = 5
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9^][A-Z0-9.\-=^]{0,14}$")
SOURCES = {"yahoo": "backcast.sources.yahoo", "csv": "backcast.sources.csv"}
DEFAULT_SOURCE = "yahoo"


class DataError(Exception):
    """Prices could not be loaded; the message is safe to show to users."""


class SourceError(Exception):
    """The configured price source can't be used: it is missing, misconfigured, or broke the contract."""


def normalize_symbol(raw: str) -> str:
    symbol = raw.strip().upper()
    if not SYMBOL_PATTERN.fullmatch(symbol):
        raise DataError(f"“{raw.strip()[:20]}” is not a valid ticker symbol.")
    return symbol


def source_name() -> str:
    return os.environ.get("BACKCAST_PRICE_SOURCE", "").strip() or DEFAULT_SOURCE


def price_source() -> ModuleType:
    return _import_source(source_name())


@cache
def _import_source(name: str) -> ModuleType:
    try:
        module = importlib.import_module(SOURCES.get(name, name))
    except ImportError as exc:
        raise SourceError(
            f"BACKCAST_PRICE_SOURCE is {name!r}, which couldn't be imported ({exc}). Use yahoo, csv, or the import "
            "path of a module that defines load_prices(symbol, years)."
        ) from exc
    if not callable(getattr(module, "load_prices", None)):
        raise SourceError(f"The price source {name!r} has no load_prices(symbol, years) function.")
    return module


def attribution() -> str:
    source = price_source()
    return getattr(source, "ATTRIBUTION", f"Daily closes from the {source_name()} price source.")


def load_prices(symbol: str, years: int = YEARS) -> pd.Series:
    """Daily closes adjusted for splits only, oldest first, covering at most the last `years` years.

    `attrs["calendar"]` names the trading calendar (see `trading_days_after`).
    """
    return _checked(price_source().load_prices(symbol, years), symbol, years)


def search_symbols(query: str, limit: int = 8) -> list[dict]:
    search = getattr(price_source(), "search_symbols", None)
    return search(query, limit) if search else []


def _checked(close, symbol: str, years: int) -> pd.Series:
    """Whatever the source, the backtest gets one positive close per trading date, oldest first."""
    source = source_name()
    if not isinstance(close, pd.Series) or not isinstance(close.index, pd.DatetimeIndex):
        raise SourceError(f"The {source} price source must return a pandas Series indexed by date.")
    calendar = close.attrs.get("calendar")
    if calendar not in (None, "24/7", "weekdays") and calendar not in xcals.get_calendar_names():
        raise SourceError(f"The {source} price source set an unknown trading calendar, {calendar!r}.")
    try:
        close = close.astype(float).dropna()
    except (TypeError, ValueError) as exc:
        raise DataError(f"The {symbol} prices from {source} include values that aren't numbers.") from exc
    if close.empty:
        raise DataError(f"No price history found for {symbol}.")
    dates = close.index.tz_localize(None) if close.index.tz else close.index
    close.index = dates.normalize()
    close = close.sort_index()
    if (repeated := close.index[close.index.duplicated()]).size:
        raise DataError(f"The {symbol} prices from {source} have more than one close on {repeated[0]:%d %b %Y}.")
    if (invalid := close.index[~(np.isfinite(close) & (close > 0))]).size:
        raise DataError(f"The {symbol} prices from {source} have a close that isn't positive on {invalid[0]:%d %b %Y}.")
    close = close.loc[close.index[-1] - pd.DateOffset(years=years) :].rename(symbol)
    close.attrs = {"calendar": calendar}
    return close


def trading_days_after(last: pd.Timestamp, count: int, calendar: str | None) -> pd.DatetimeIndex:
    """The `count` trading days after `last`. An unknown calendar falls back to weekdays, holidays included."""
    first = last + pd.Timedelta(days=1)
    if calendar == "24/7":
        return pd.date_range(first, periods=count)
    if calendar in (None, "weekdays"):
        return pd.bdate_range(first, periods=count)
    # Twice the count covers weekends; two more weeks cover the longest holiday runs (Lunar New Year, Golden Week).
    return xcals.get_calendar(calendar).sessions_in_range(first, last + pd.Timedelta(days=2 * count + 14))[:count]
