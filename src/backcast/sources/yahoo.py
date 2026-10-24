"""The default price source: Yahoo Finance, through yfinance."""

import warnings

import pandas as pd
import yfinance as yf

from ..data import DataError

# yfinance trips pandas deprecation warnings internally; they say nothing about our code.
warnings.filterwarnings("ignore", module=r"yfinance\.")

ATTRIBUTION = "Daily closes from Yahoo Finance, through yfinance. Prices may be delayed."


def load_prices(symbol: str, years: int) -> pd.Series:
    """Daily closes as they traded, adjusted for splits only, indexed by trading date.

    Dividend adjustment is left out on purpose: it rescales every earlier price by later dividends, so past prices
    would not be the prices anyone saw. `attrs["calendar"]` names the trading calendar (see `calendar_for`).
    """
    try:
        ticker = yf.Ticker(symbol)
        history = ticker.history(period=f"{years}y", interval="1d", auto_adjust=False)
        metadata = ticker.history_metadata or {}
    except Exception as exc:  # yfinance raises many unrelated exception types
        raise DataError(f"Couldn't reach Yahoo Finance for {symbol}. Try again in a minute.") from exc
    if history.empty or "Close" not in history:
        raise DataError(f"No price history found for {symbol}.")
    close = history["Close"].dropna()
    close.index = close.index.tz_localize(None).normalize()
    close = drop_unfinished_day(close, metadata, pd.Timestamp.now(tz="UTC")).rename(symbol)
    close.attrs["calendar"] = calendar_for(metadata)
    return close


def drop_unfinished_day(close: pd.Series, metadata: dict, now: pd.Timestamp) -> pd.Series:
    """While a session is trading, Yahoo already returns a bar for it whose "close" is just the latest price."""
    end = metadata.get("currentTradingPeriod", {}).get("regular", {}).get("end")
    if end is None or close.empty:
        return close
    end = pd.Timestamp(end, unit="s", tz="UTC") if isinstance(end, int | float) else pd.Timestamp(end)
    timezone = metadata.get("exchangeTimezoneName") or "UTC"
    session_date = end.tz_convert(timezone).normalize().tz_localize(None)
    if close.index[-1] == session_date and now < end:
        return close.iloc[:-1]
    return close


# Yahoo exchange code -> exchange_calendars code. Every US venue follows the NYSE holiday calendar.
US_EXCHANGES = ("NMS", "NGM", "NCM", "NYQ", "PCX", "ASE", "BTS", "PNK", "SNP", "DJI", "NIM", "WCB")
EXCHANGE_CALENDARS = dict.fromkeys(US_EXCHANGES, "XNYS") | {
    "TOR": "XTSE",
    "VAN": "XTSE",
    "MEX": "XMEX",
    "SAO": "BVMF",
    "LSE": "XLON",
    "ISE": "XDUB",
    "GER": "XETR",
    "FRA": "XFRA",
    "PAR": "XPAR",
    "AMS": "XAMS",
    "BRU": "XBRU",
    "LIS": "XLIS",
    "MIL": "XMIL",
    "MCE": "XMAD",
    "EBS": "XSWX",
    "VIE": "XWBO",
    "STO": "XSTO",
    "CPH": "XCSE",
    "HEL": "XHEL",
    "OSL": "XOSL",
    "IST": "XIST",
    "TLV": "XTAE",
    "JNB": "XJSE",
    "JPX": "XTKS",
    "HKG": "XHKG",
    "SHH": "XSHG",
    "SHZ": "XSHG",
    "KSC": "XKRX",
    "KOE": "XKRX",
    "TAI": "XTAI",
    "TWO": "XTAI",
    "SES": "XSES",
    "BSE": "XBOM",
    "NSI": "XBOM",
    "ASX": "XASX",
    "NZE": "XNZE",
}


def calendar_for(metadata: dict) -> str | None:
    """An exchange_calendars code, "24/7" for crypto, "weekdays" for currencies, or None when unknown."""
    if metadata.get("instrumentType") == "CRYPTOCURRENCY":
        return "24/7"
    if metadata.get("instrumentType") == "CURRENCY":
        return "weekdays"
    return EXCHANGE_CALENDARS.get(metadata.get("exchangeName"))


def search_symbols(query: str, limit: int) -> list[dict]:
    try:
        quotes = yf.Search(query, max_results=limit, news_count=0, lists_count=0, recommended=0).quotes
    except Exception:  # search is a convenience; failures just mean no suggestions
        return []
    return [
        {
            "symbol": q["symbol"],
            "name": q.get("shortname") or q.get("longname") or "",
            "exchange": q.get("exchDisp", ""),
        }
        for q in quotes
        if q.get("symbol") and q.get("quoteType") in {"EQUITY", "ETF", "INDEX"}
    ]
