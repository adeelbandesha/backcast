import sys
import types
from importlib.metadata import EntryPoint, EntryPoints

import numpy as np
import pandas as pd
import pytest

from backcast import data
from backcast.data import DataError, SourceError, attribution, load_prices, search_symbols


@pytest.fixture
def source(monkeypatch):
    """Installs a throwaway price source module whose load_prices returns `source.close`."""
    module = types.ModuleType("fake_prices")
    module.load_prices = lambda symbol, years: module.close
    monkeypatch.setitem(sys.modules, "fake_prices", module)
    monkeypatch.setenv("BACKCAST_PRICE_SOURCE", "fake_prices")
    data._import_source.cache_clear()
    yield module
    data._import_source.cache_clear()


@pytest.fixture
def csv_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKCAST_PRICE_SOURCE", "csv")
    monkeypatch.setenv("BACKCAST_CSV_DIR", str(tmp_path))
    return tmp_path


def series(values, dates) -> pd.Series:
    return pd.Series(values, index=pd.DatetimeIndex(dates), dtype=object)


def test_yahoo_is_the_default(monkeypatch):
    monkeypatch.delenv("BACKCAST_PRICE_SOURCE", raising=False)
    assert data.price_source().__name__ == "backcast.sources.yahoo"
    assert "Yahoo Finance" in attribution()


def test_closes_are_sorted_dated_and_cleaned(source):
    dates = pd.DatetimeIndex(["2026-10-23 16:00", "2026-10-21 16:00", "2026-10-22 16:00"], tz="America/New_York")
    source.close = pd.Series([12.0, 10.0, np.nan], index=dates)
    source.close.attrs["calendar"] = "XNYS"
    close = load_prices("abc")
    assert list(close.index) == list(pd.to_datetime(["2026-10-21", "2026-10-23"]))
    assert list(close) == [10.0, 12.0] and close.name == "abc"
    assert close.attrs == {"calendar": "XNYS"}


def test_history_is_trimmed_to_the_years_asked_for(source):
    dates = pd.bdate_range("2010-01-01", "2026-10-23")
    source.close = pd.Series(np.linspace(1, 2, len(dates)), index=dates)
    close = load_prices("ABC", years=5)
    assert close.index[0] >= pd.Timestamp("2021-10-23") and close.index[-1] == pd.Timestamp("2026-10-23")


@pytest.mark.parametrize(
    ("values", "dates", "message"),
    [
        ([1.0, 2.0], ["2026-10-22", "2026-10-22 18:00"], "more than one close on 22 Oct 2026"),
        ([1.0, -2.0], ["2026-10-22", "2026-10-23"], "isn't positive on 23 Oct 2026"),
        ([1.0, np.inf], ["2026-10-22", "2026-10-23"], "isn't positive on 23 Oct 2026"),
        ([1.0, "n/a"], ["2026-10-22", "2026-10-23"], "aren't numbers"),
        ([np.nan], ["2026-10-22"], "No price history found for ABC"),
    ],
)
def test_bad_prices_are_refused_with_a_reason(source, values, dates, message):
    source.close = series(values, dates)
    with pytest.raises(DataError, match=message):
        load_prices("ABC")


def test_sources_that_break_the_contract_are_named(source):
    source.close = [1.0, 2.0]
    with pytest.raises(SourceError, match="must return a pandas Series"):
        load_prices("ABC")
    source.close = series([1.0], ["2026-10-23"])
    source.close.attrs["calendar"] = "Wall Street"
    with pytest.raises(SourceError, match="unknown trading calendar"):
        load_prices("ABC")


def test_the_built_in_sources_are_registered_by_name():
    assert {"yahoo", "csv"} <= {entry.name for entry in data.entry_points(group=data.ENTRY_POINTS)}


def test_installed_packages_can_add_sources(source, monkeypatch):
    plugins = [EntryPoint("licensed", "fake_prices", data.ENTRY_POINTS)]
    monkeypatch.setattr(data, "entry_points", lambda **params: EntryPoints(plugins).select(**params))
    monkeypatch.setenv("BACKCAST_PRICE_SOURCE", "licensed")
    assert data.price_source() is source

    plugins.append(EntryPoint("licensed", "other_prices", data.ENTRY_POINTS))
    data._import_source.cache_clear()
    with pytest.raises(SourceError, match="More than one installed package registers the price source 'licensed'"):
        data.price_source()


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("no_such_module_here", r"couldn't be imported .*Use an installed source \(.*csv.*yahoo"),
        ("json", "has no load_prices"),
    ],
)
def test_unusable_sources_fail_clearly(monkeypatch, name, message):
    monkeypatch.setenv("BACKCAST_PRICE_SOURCE", name)
    with pytest.raises(SourceError, match=message):
        data.price_source()


def test_optional_parts_of_a_source(source):
    assert search_symbols("abc") == []
    assert attribution() == "Daily closes from the fake_prices price source."
    source.ATTRIBUTION = "Closes from our own feed."
    assert attribution() == "Closes from our own feed."


def test_csv_uses_close_and_never_adjusted_close(csv_folder):
    (csv_folder / "aapl.csv").write_text(
        "Date,Open,High,Low,Close,Adj Close,Volume\n2026-10-22,1,1,1,100.5,99.1,10\n2026-10-23,1,1,1,101.25,99.9,10\n"
    )
    close = load_prices("AAPL")
    assert list(close) == [100.5, 101.25]
    assert close.attrs == {"calendar": None}


def test_csv_calendar_can_be_set(csv_folder, monkeypatch):
    (csv_folder / "SHOP.csv").write_text("date,close\n2026-10-23,80\n")
    monkeypatch.setenv("BACKCAST_CSV_CALENDAR", "XTSE")
    assert load_prices("SHOP").attrs == {"calendar": "XTSE"}


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        ("day,price\n2026-10-23,1\n", "needs a Date column and a Close column"),
        ("date,close\nyesterday,1\n", "has a date that can't be read"),
    ],
)
def test_csv_problems_are_explained(csv_folder, contents, message):
    (csv_folder / "BAD.csv").write_text(contents)
    with pytest.raises(DataError, match=message):
        load_prices("BAD")


def test_csv_missing_file_and_folder(csv_folder, monkeypatch):
    with pytest.raises(DataError, match="no price file for MSFT"):
        load_prices("MSFT")
    monkeypatch.setenv("BACKCAST_CSV_DIR", str(csv_folder / "missing"))
    with pytest.raises(SourceError, match="BACKCAST_CSV_DIR"):
        load_prices("MSFT")


def test_csv_search_lists_matching_files(csv_folder):
    for name in ("SPY.csv", "XSP.csv", "KO.csv", "notes.txt"):
        (csv_folder / name).write_text("date,close\n2026-10-23,1\n")
    assert [result["symbol"] for result in search_symbols("sp")] == ["SPY", "XSP"]
    assert search_symbols("ko") == [{"symbol": "KO", "name": "KO.csv", "exchange": "CSV"}]
