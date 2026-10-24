# Backcast

[![CI](https://github.com/adeelbandesha/backcast/actions/workflows/ci.yml/badge.svg)](https://github.com/adeelbandesha/backcast/actions/workflows/ci.yml)
![Python 3.13](https://img.shields.io/badge/python-3.13-blue)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Backcast checks whether any forecasting model could have predicted a stock's price better than the laziest guess
there is: that the price a few days from now will be the same as it is today.

Most of the time the answer is no. Daily stock prices behave a lot like a random walk, and against a random walk the
naive forecast is very hard to beat. Plenty of stock prediction demos never check this. They draw the model's
predictions over the real price, the two lines look close, and that gets taken as proof the model works. A forecast
that just copies yesterday's price would look every bit as close. Backcast scores each model against that copy,
only on prices the model hadn't seen, and then tests whether the difference is bigger than luck.

![Backtest results for AAPL at a 5-day horizon: the verdict, the trend note, the SPA p-value and the leaderboard](docs/results.png)

## How it works

You give it a ticker and a horizon of 1, 5 or 20 trading days. Backcast then:

1. loads five years of daily closes, adjusted for splits but not for dividends, from Yahoo Finance or from
   [another price source](docs/price-sources.md);
2. makes a forecast from every one of the last 504 trading days (about two years), each model seeing only the
   prices up to that day and forecasting the close `h` trading days later. Models are re-estimated every 20 trading
   days and keep their parameters in between, while still seeing each new close;
3. compares each forecast with the price that actually came, on log prices so a 2% miss counts the same at any
   price level, and scores its error, its whole forecast distribution (CRPS), its 80% interval, how often it beat
   naive and how often it called the direction.

Forecasts made a day apart share most of their future, so their errors are correlated and a plain t-test finds
edges that aren't there. In a simulation with no real edge it called 20-day results significant two times out of
three. Backcast uses the Diebold-Mariano test, which allows for the overlap and was wrong 5 to 7% of the time at the
5% level.

The headline uses a different test. Picking the best of five models and testing it alone overstates the evidence,
so the verdict comes from Hansen's test for superior predictive ability (SPA), which bootstraps all five models
together and asks whether the best of them beats naive by more than luck would. Results are also split by whether
the stock was above or below its 200-day average on the forecast date, and when the best model won by betting the
same direction nearly every day of a period that trended that way, the verdict says so.
[Reading the results](docs/reading-results.md) goes through all of this in detail.

## Getting started

You need [uv](https://docs.astral.sh/uv/). It will download Python 3.13 if you don't have it.

```bash
uv sync
uv run backcast run SPY --horizon 5
```

If you'd rather use pip, any Python 3.13 environment works. `requirements.txt` pins the same versions as `uv.lock`:

```bash
pip install -r requirements.txt
pip install -e .
backcast run SPY --horizon 5
```

A run takes 10 to 20 seconds on a four-core laptop; the work is spread across your CPU cores. This is a real run
from 23 October 2026:

```
SPY: 5-day forecasts from 504 trading days

Drift edged out the naive forecast by 1.2%, but the margin is within the noise. It predicted a rise on all 504 days while the price rose 30%, so the win says more about this period's trend than about forecasting skill.
SPA p-value that no model beats naive: 0.268

   model  mae_pct  relative_mae  relative_crps  relative_interval  win_rate  tie_rate  p_value  direction  coverage  p_adjusted
   Drift    1.504         0.988          0.995              0.996     0.560     0.000    0.231      0.575     0.895       0.925
   Theta    1.506         0.990          1.005              0.983     0.563     0.000    0.126      0.575     0.889       0.630
   Naive    1.521         1.000          1.000              1.000         —         —        —          —     0.899           —
     ETS    1.522         1.000          1.000              1.001     0.522     0.000    0.383      0.522     0.901       0.925
   ARIMA    1.526         1.003          1.002              1.006     0.131     0.722    0.500      0.515     0.899       0.925
LightGBM    1.549         1.018          0.982              0.906     0.478     0.000    0.276      0.522     0.728       0.925
```

Drift came out on top by predicting a rise every single day while SPY climbed 30%, which is a bet on the trend, not
a forecast. No model's edge survives the tests: the best Diebold-Mariano p-value is Theta's 0.126 before adjusting
for five tries, and the SPA test puts the chance that the best result is luck at 27%. ARIMA tied naive on 72% of
days, because it usually settles on a random walk.

The intervals are worth a look too. Most models' 80% intervals held the actual close about 90% of the time, so
they were too wide. Daily returns have fat tails: a few very large days inflate the volatility the intervals are
built from, which leaves them wider than a typical week needs. LightGBM's quantile regression has the opposite
problem at 73%, yet it has the best interval score: its narrower intervals saved more than its extra misses cost.

[The command line](docs/command-line.md) explains every column.

## The web app

```bash
uv run backcast web
```

Then open http://127.0.0.1:8000 and search for a company or ticker. Each backtest page has the verdict, the
leaderboard, the same comparison split into up and down markets, a chart of the next few trading days as each model
sees them, and a running total of how far the chosen model has pulled ahead of naive, or fallen behind, over the
two years. Every chart has a table of the numbers behind it, and there is a dark
theme.

![The forecast chart and the running total against naive for AAPL, comparing Drift with naive](docs/charts.png)

## The models

| Model    | Kind             | Notes                                                                                          |
| -------- | ---------------- | ---------------------------------------------------------------------------------------------- |
| Naive    | Baseline         | Tomorrow's price is today's. The benchmark.                                                    |
| Drift    | Baseline         | Naive plus the average daily percentage change over the training data.                         |
| ETS      | Statistical      | Exponential smoothing, components picked automatically.                                        |
| ARIMA    | Statistical      | Auto-ARIMA.                                                                                    |
| Theta    | Statistical      | The Theta method, which did well in the M3 competition.                                        |
| LightGBM | Machine learning | Gradient-boosted trees on the last 20 daily log returns, quantile regression for the interval. |

ETS, ARIMA and Theta come from [statsforecast](https://github.com/Nixtla/statsforecast). Every model is fitted to
log prices and its forecasts converted back, so none can forecast a price below zero, and each one's point forecast
is the median of its distribution.

Adding a model takes one class. [The models](docs/models.md) shows how, and what every model has to guarantee.

## Price data

Prices come from Yahoo Finance by default, with no account or key needed. You can switch to your own CSV files with
two environment variables, or connect any other provider by writing a module with one function:

```bash
BACKCAST_PRICE_SOURCE=csv BACKCAST_CSV_DIR=~/prices uv run backcast run AAPL
BACKCAST_PRICE_SOURCE=mysources.feed uv run backcast web
```

Whatever the source, Backcast checks the prices before a backtest sees them: one positive close per trading day,
oldest first, trimmed to the same five years. [Price sources](docs/price-sources.md) covers the file format, the
contract for your own source and the checks.

## Documentation

- [Getting started](docs/getting-started.md)
- [The command line](docs/command-line.md)
- [The web app](docs/web-app.md)
- [Reading the results](docs/reading-results.md)
- [The models](docs/models.md)
- [Price sources](docs/price-sources.md)
- [Configuration](docs/configuration.md)
- [Deployment](docs/deployment.md)
- [Python API](docs/python.md)

## Project layout

```
src/backcast/
  data.py        loads prices from the configured source and checks them; trading calendars
  sources/       the built-in price sources: Yahoo Finance and CSV files
  models.py      the forecasting models
  evaluate.py    walk-forward backtest, metrics, significance tests, the verdict
  cli.py         the backcast command
  web/           Django app: views, templates, CSS, and the charts (Observable Plot)
docs/            the user guides
tests/           engine, data, price source and view tests; none of them touch the network
scripts/         refit_check.py, the experiment behind the refit interval
```

## Deploying

The web app is a small Django project with no database and no user accounts, run with Gunicorn behind a reverse
proxy. [Deployment](docs/deployment.md) covers the settings, workers, caching and rate limiting, and
[Configuration](docs/configuration.md) lists every environment variable.

## Limits

Read the results with these in mind:

- Two years is one stretch of market history. A result can hold over these two years and not the next two.
- A stock needs about two and a half years of history before it can be tested: 500 days before the first forecast
  and at least 100 forecasts after it.
- Only the price history goes in. There's no volume, no fundamentals, no news.
- Errors are measured on prices, not on trading returns. A model that beats naive here could still lose money once
  you add costs.
- Dividends aren't added back. On the day a stock goes ex-dividend its price drops by about the dividend, and none
  of the models forecast that.
- Future dates follow the exchange's holiday calendar for most major exchanges. For the rest, they skip weekends
  only.
- By default, prices come through [yfinance](https://github.com/ranaroussi/yfinance), an unofficial Yahoo Finance
  client, and Yahoo's terms apply to the data.

None of this is investment advice.

## Planned

What I'm working on next. None of it exists yet.

- Long backtests as background jobs, with saved results you can link to.
- The same backtest across the S&P 500, with the results published.
- A pretrained time-series foundation model (Chronos-Bolt), and a volatility track. Volatility, unlike price, is
  known to be partly predictable, so models there have a much better record against naive.

## Development

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

[CONTRIBUTING.md](CONTRIBUTING.md) covers the checks CI runs, what the tests cover, and the refit experiment.

## Contributing

Fixes and new models are welcome. [CONTRIBUTING.md](CONTRIBUTING.md) covers setup, the checks CI runs, and what a
new model has to guarantee. Please report security problems privately, as described in [SECURITY.md](SECURITY.md).

## License

MIT. See [LICENSE](LICENSE).

The web app bundles a few things written by other people. [D3](https://d3js.org) and
[Observable Plot](https://observablehq.com/plot/) are under the ISC license. The IBM Plex fonts are under the SIL Open
Font License, and a copy of it sits next to the font files.
