# Price sources

A price source is where Backcast gets its daily closes. There are two built in, and you can add your own with a
short Python module.

| Source  | Setting                           | Needs                      | Symbol search          |
| ------- | --------------------------------- | -------------------------- | ---------------------- |
| Yahoo   | none, it's the default            | an internet connection     | Yahoo's search         |
| CSV     | `BACKCAST_PRICE_SOURCE=csv`       | a folder of CSV files      | the files in the folder |
| Your own | `BACKCAST_PRICE_SOURCE=your.module` | a module you write     | optional               |

The setting applies to the command line, the web app and the Python API alike.

## Yahoo Finance (the default)

With nothing configured, Backcast downloads prices from Yahoo Finance through
[yfinance](https://github.com/ranaroussi/yfinance). It needs no account or API key.

Backcast asks for the closes as they traded, adjusted for stock splits but not for dividends, and leaves out
today's price while the market is still open. It also reads which exchange the symbol trades on, so future dates in
the forecast chart follow that exchange's holidays.

Two things to know:

- yfinance is an unofficial client. Yahoo has no public API, and its terms allow personal use. Running Backcast for
  yourself is fine. Before you serve Yahoo data on a public website, read Yahoo's terms, or switch the site to a
  provider whose licence allows it.
- Yahoo sometimes throttles or blocks requests, especially from cloud servers, and occasionally changes the pages
  yfinance reads until yfinance catches up. When that happens Backcast says it couldn't reach Yahoo Finance.
  Waiting a while or upgrading yfinance usually fixes it.

## CSV files

The CSV source reads prices from files you provide: your own data, an export from your broker, or prices from a
provider you already pay for. It needs no network connection.

```bash
export BACKCAST_PRICE_SOURCE=csv
export BACKCAST_CSV_DIR=/path/to/prices
backcast run AAPL
```

Put one file per symbol in the folder, named after the symbol: `AAPL.csv`, `BRK-B.csv`, `SHOP.TO.csv`. The case of
the name doesn't matter. Each file needs a `Date` column and a `Close` column; other columns are ignored, so a
standard export with Open, High, Low, Close, Adj Close and Volume works as it is.

```
Date,Close
2021-10-25,148.64
2021-10-26,149.32
2021-10-27,148.85
```

The rules for the data:

- **Adjust the closes for splits, not for dividends.** Close prices from most providers are already like this. Do
  not use an Adj Close column: dividend adjustment rescales every earlier price, so the history no longer shows the
  prices anyone could trade at. Backcast reads the `Close` column and never `Adj Close`.
- **Only finished trading days.** Leave out a row for today until the market has closed, or its "close" is just the
  latest price.
- **One row per trading day.** Order doesn't matter, and empty rows are skipped. Any date format pandas can read
  works, as long as the whole file uses the same one.
- **Enough history.** A backtest needs about 600 trading days, two and a half years, and uses at most the last five
  years. Older rows are fine but aren't used.

Trading calendars: a CSV file doesn't say which exchange the prices come from, so by default the forecast chart's
future dates skip weekends only and the chart says so. To use an exchange's holidays, name its calendar:

```bash
export BACKCAST_CSV_CALENDAR=XNYS
```

`XNYS` covers every US exchange. Other codes come from
[exchange_calendars](https://github.com/gerrymanoim/exchange_calendars#calendars), for example `XLON` for London,
`XTSE` for Toronto and `XTKS` for Tokyo. Use `24/7` for crypto and `weekdays` for currencies. The calendar only
affects the dates shown on future forecasts; the backtest itself uses the dates in your file.

In the web app, the search box lists the files in the folder. Files you add are picked up straight away, without a
restart, though a symbol you have already run keeps its cached prices for an hour.

## Writing your own source

To use any other provider, write a Python module with a `load_prices` function and point Backcast at it:

```bash
export BACKCAST_PRICE_SOURCE=mysources.feed
```

The value is the module's import path, so the module has to be importable: installed in the same environment, or
on `PYTHONPATH`. A module is a source if it defines these names:

| Name                          | Required | What it does                                                                     |
| ----------------------------- | -------- | -------------------------------------------------------------------------------- |
| `load_prices(symbol, years)`  | yes      | Returns the daily closes for `symbol` as a pandas Series indexed by date.        |
| `search_symbols(query, limit)` | no      | Returns up to `limit` matches for the web app's search box.                      |
| `ATTRIBUTION`                 | no       | One sentence for the web app's footer, crediting where the prices come from.     |

### load_prices

Backcast calls `load_prices(symbol, years)` with an upper-case symbol and the number of years of history it wants,
currently 5. Return a pandas Series of closes with a `DatetimeIndex`:

- adjusted for splits and **not** for dividends, for the reason given under CSV files above;
- finished trading days only;
- any order, with or without time zones, with missing values allowed. Backcast sorts the series, drops missing
  values and reduces each timestamp to its date.

If you know the exchange's trading calendar, set it on the series:

```python
close.attrs["calendar"] = "XNYS"
```

Leave it unset if you don't know; the forecast chart then skips weekends only.

When the prices can't be loaded, for example an unknown symbol or a provider that is down, raise
`backcast.data.DataError` with a message a user can read. The command line prints the message and the web app shows
it on the page. Any other exception is treated as a bug: the command line shows a traceback, and the web app logs
it and shows its error page.

### search_symbols

`search_symbols(query, limit)` gets the text typed into the search box and returns a list of dictionaries:

```python
[{"symbol": "AAPL", "name": "Apple Inc.", "exchange": "NASDAQ"}]
```

All three keys are shown in the suggestions. If the module has no `search_symbols`, the search box shows no
suggestions, and typing a ticker still works.

### An example

A source for a provider whose API returns raw closes with a split factor for each day, as several do. The adjustment
divides every close by the product of all the splits after it, so a 10-for-1 split doesn't look like a 90% crash:

```python
"""mysources/feed.py: daily closes from our market data provider."""

import pandas as pd

from backcast.data import DataError

from .client import FeedClient, FeedError  # your provider's client library

ATTRIBUTION = "Daily closes from Example Feed."

client = FeedClient()  # reads its API key from the environment


def load_prices(symbol: str, years: int) -> pd.Series:
    start = pd.Timestamp.today() - pd.DateOffset(years=years)
    try:
        rows = client.daily_bars(symbol, start=start.date())
    except FeedError as error:
        raise DataError(f"Couldn't load {symbol} from Example Feed: {error}") from error
    if not rows:
        raise DataError(f"Example Feed has no prices for {symbol}.")
    table = pd.DataFrame(rows)
    table.index = pd.to_datetime(table.pop("date"))
    table = table.sort_index()
    later_splits = table.split_factor[::-1].cumprod()[::-1].shift(-1, fill_value=1.0)
    close = table.close / later_splits
    close.attrs["calendar"] = "XNYS"
    return close


def search_symbols(query: str, limit: int) -> list[dict]:
    matches = client.search(query)[:limit]
    return [{"symbol": m["ticker"], "name": m["name"], "exchange": m["exchange"]} for m in matches]
```

`FeedClient` stands in for your provider's real client, and the column names will differ. If the provider already
returns split-adjusted closes without dividend adjustment, skip the split arithmetic and return them as they are.

### What Backcast checks

Every source goes through the same checks, so a backtest only ever sees clean data:

| Problem                                  | What happens                                                              |
| ---------------------------------------- | ------------------------------------------------------------------------- |
| Not a pandas Series with a date index    | `SourceError` naming the source: a bug in the source.                      |
| `attrs["calendar"]` is an unknown code   | `SourceError` naming the code.                                             |
| Values that aren't numbers               | `DataError`: the prices for this symbol can't be used.                     |
| Two closes on the same date              | `DataError` giving the date.                                               |
| A close of zero, below zero or infinite  | `DataError` giving the date.                                               |
| Nothing left after dropping missing values | `DataError`: no price history found.                                      |
| More history than asked for              | Trimmed to the last `years` years, so every source trains on the same span. |

If `BACKCAST_PRICE_SOURCE` names a module that can't be imported, or one without `load_prices`, the command line
stops with an explanation and the web app refuses to start, so a typo never reaches your visitors.

### Testing your source

Load a symbol you know and look at the result before running a backtest:

```python
from backcast.data import load_prices

close = load_prices("AAPL")
print(close.tail(), close.attrs)
```

`load_prices` here is Backcast's, so it runs your source and then the checks above. To check the split handling,
look at the days around a known split: the closes on either side should be close to each other, not ten times
apart. To check the dividend handling, look at an ex-dividend day: the close should drop by about the dividend, and
the prices before it should match what the stock actually traded at.

## Caching

The web app keeps each symbol's prices for an hour and each backtest for six hours. Each source has its own cache
entries, so switching sources never shows prices or results from the previous one. The command line and the Python
API don't cache: every run loads fresh prices.
