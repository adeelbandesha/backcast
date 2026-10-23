import pickle

import pandas as pd
import pytest

from backcast.data import DataError, calendar_for, drop_unfinished_day, normalize_symbol, trading_days_after

NEW_YORK = {
    "exchangeTimezoneName": "America/New_York",
    "currentTradingPeriod": {"regular": {"end": pd.Timestamp("2026-10-23 16:00", tz="America/New_York")}},
}


def closes(*dates) -> pd.Series:
    return pd.Series(range(len(dates)), index=pd.DatetimeIndex(dates), dtype=float)


def test_today_is_dropped_while_the_market_is_open():
    close = closes("2026-10-22", "2026-10-23")
    during = pd.Timestamp("2026-10-23 13:58", tz="America/New_York")
    after = pd.Timestamp("2026-10-23 16:05", tz="America/New_York")
    assert list(drop_unfinished_day(close, NEW_YORK, during).index) == [pd.Timestamp("2026-10-22")]
    assert len(drop_unfinished_day(close, NEW_YORK, after)) == 2


def test_a_finished_session_is_kept_over_the_weekend():
    # On Saturday, Yahoo's "current" session is Monday's, and Friday's close is final.
    monday = {**NEW_YORK, "currentTradingPeriod": {"regular": {"end": pd.Timestamp("2026-10-26 16:00-04:00")}}}
    close = closes("2026-10-22", "2026-10-23")
    assert len(drop_unfinished_day(close, monday, pd.Timestamp("2026-10-24 12:00", tz="UTC"))) == 2


def test_session_end_given_as_epoch_seconds():
    metadata = {"exchangeTimezoneName": "UTC", "currentTradingPeriod": {"regular": {"end": 1792799940}}}  # 23:59 UTC
    close = closes("2026-10-22", "2026-10-23")
    assert len(drop_unfinished_day(close, metadata, pd.Timestamp("2026-10-23 18:00", tz="UTC"))) == 1


def test_missing_metadata_keeps_everything():
    close = closes("2026-10-22", "2026-10-23")
    assert len(drop_unfinished_day(close, {}, pd.Timestamp("2026-10-23 12:00", tz="UTC"))) == 2


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({"exchangeName": "NMS", "instrumentType": "EQUITY"}, "XNYS"),
        ({"exchangeName": "PCX", "instrumentType": "ETF"}, "XNYS"),
        ({"exchangeName": "TOR", "instrumentType": "EQUITY"}, "XTSE"),
        ({"exchangeName": "LSE", "instrumentType": "EQUITY"}, "XLON"),
        ({"exchangeName": "CCC", "instrumentType": "CRYPTOCURRENCY"}, "24/7"),
        ({"exchangeName": "CCY", "instrumentType": "CURRENCY"}, "weekdays"),
        ({"exchangeName": "XYZ", "instrumentType": "EQUITY"}, None),
        ({}, None),
    ],
)
def test_calendar_for(metadata, expected):
    assert calendar_for(metadata) == expected


def test_new_york_skips_thanksgiving_and_christmas():
    days = trading_days_after(pd.Timestamp("2026-11-24"), 25, "XNYS")
    assert pd.Timestamp("2026-11-26") not in days  # Thanksgiving
    assert pd.Timestamp("2026-12-25") not in days
    assert days[0] == pd.Timestamp("2026-11-25") and days[1] == pd.Timestamp("2026-11-27")
    assert len(days) == 25


def test_crypto_trades_every_day():
    days = trading_days_after(pd.Timestamp("2026-10-23"), 3, "24/7")  # a Friday
    assert list(days) == list(pd.to_datetime(["2026-10-24", "2026-10-25", "2026-10-26"]))


@pytest.mark.parametrize("calendar", [None, "weekdays"])
def test_unknown_calendars_skip_weekends(calendar):
    days = trading_days_after(pd.Timestamp("2026-10-23"), 2, calendar)
    assert list(days) == list(pd.to_datetime(["2026-10-26", "2026-10-27"]))


def test_calendar_survives_the_cache():
    close = closes("2026-10-22", "2026-10-23")
    close.attrs["calendar"] = "XNYS"
    assert pickle.loads(pickle.dumps(close)).attrs == {"calendar": "XNYS"}


@pytest.mark.parametrize("raw", ["", "$$$", "A" * 20, "AAPL; DROP"])
def test_invalid_symbols(raw):
    with pytest.raises(DataError):
        normalize_symbol(raw)


def test_symbols_are_normalized():
    assert normalize_symbol("  brk-b ") == "BRK-B"
    assert normalize_symbol("^gspc") == "^GSPC"
