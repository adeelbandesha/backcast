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

![Backtest results for AAPL at a 5-day horizon: the verdict, the trend note and the leaderboard](docs/results.png)

## How it works

You give it a ticker and a horizon of 1, 5 or 20 trading days. Backcast then:

1. downloads five years of daily closes from Yahoo Finance, adjusted for splits but not for dividends, and leaves
   out today's price if the market hasn't closed yet;
2. picks 30 forecast dates, counting back from the latest close one horizon at a time so that no two forecasts
   overlap;
3. on each of those dates, fits every model to the prices up to that day and forecasts the close `h` trading days
   later;
4. compares each forecast with the price that actually came, and scores it.

| Column (CLI name)             | What it measures                                                                                 |
| ----------------------------- | ------------------------------------------------------------------------------------------------ |
| Mean error (`mae`)            | Average absolute gap between forecast and actual close at the horizon, in the stock's currency.  |
| vs naive (`relative_mae`)     | Mean error relative to naive's. The leaderboard is sorted by it, and lower is better.            |
| Windows won (`win_rate`)      | Share of the 30 windows in which the model was closer than naive.                                |
| Direction right (`direction`) | How often the forecast moved the same way as the price. Forecasts of no change are left out.     |
| In 80% interval (`coverage`)  | How often the actual close fell inside the model's 80% interval. Near 80% means well calibrated. |
| p-value (`p_adjusted`)        | Paired t-test on the per-window errors against naive, adjusted for testing five models at once.  |

A model only counts as a significant winner when its adjusted p-value is below 0.05. The adjustment (Holm's method)
matters because every extra model is another chance to get lucky. When the best model won by betting the same
direction in nearly every window, during a period that happened to trend that way, the verdict says so. The Method
page in the web app goes through all of this in more detail.

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

A run takes about ten seconds; the windows are spread across your CPU cores. This is a real run from 23 October
2026:

```
SPY: 5-day forecasts over 30 windows

LightGBM edged out the naive forecast by 3.7%, but the margin is within the noise.

   model    mae  relative_mae  win_rate  p_value  direction  coverage  p_adjusted
LightGBM 10.398         0.963     0.467    0.560      0.500     0.733       1.000
   Theta 10.651         0.987     0.567    0.308      0.567     0.700       1.000
   Drift 10.675         0.989     0.567    0.600      0.567     0.733       1.000
   ARIMA 10.774         0.998     0.433    0.928      0.520     0.767       1.000
   Naive 10.793         1.000         —        —          —     0.733           —
     ETS 11.030         1.022     0.367    0.155      0.367     0.733       0.777
```

Four models came in slightly ahead of naive, and none by enough to mean anything. LightGBM had the lowest average
error while winning fewer than half the windows: its wins were bigger than its losses, not more frequent.

## The web app

```bash
uv run backcast web
```

Then open http://127.0.0.1:8000 and search for a company or ticker. Each backtest page has the verdict, the
leaderboard, a chart of the next few trading days as each model sees them, and a window-by-window chart of where the
chosen model beat naive and where it didn't. Every chart has a table of the numbers behind it, and there is a dark
theme.

![The forecast chart and the window-by-window chart for AAPL, comparing Drift with naive](docs/charts.png)

## The models

| Model    | Kind             | Notes                                                                                          |
| -------- | ---------------- | ---------------------------------------------------------------------------------------------- |
| Naive    | Baseline         | Tomorrow's price is today's. The benchmark.                                                    |
| Drift    | Baseline         | Naive plus the average daily change over the training data.                                    |
| ETS      | Statistical      | Exponential smoothing, components picked automatically.                                        |
| ARIMA    | Statistical      | Auto-ARIMA.                                                                                    |
| Theta    | Statistical      | The Theta method, which did well in the M3 competition.                                        |
| LightGBM | Machine learning | Gradient-boosted trees on the last 20 daily log returns, quantile regression for the interval. |

ETS, ARIMA and Theta come from [statsforecast](https://github.com/Nixtla/statsforecast).

To add a model, subclass `Model` in `src/backcast/models.py`, implement `forecast(y, h)` so it returns a `Forecast`
with mean, lower and upper arrays of length `h`, and append an instance to `MODELS`. The CLI, the leaderboard and the
charts will include it. Windows run in worker processes, so the model has to be picklable. Defining the class at
module level is enough.

## Project layout

```
src/backcast/
  data.py        prices from Yahoo Finance, trading calendars, symbol search
  models.py      the forecasting models
  evaluate.py    walk-forward backtest, metrics, significance tests, the verdict
  cli.py         the backcast command
  web/           Django app: views, templates, CSS, and the charts (Observable Plot)
tests/           engine, data and view tests; none of them touch the network
```

## Deploying

The web app is a small Django project with no database and no user accounts. Run it with Gunicorn:

```bash
BACKCAST_SECRET_KEY=... BACKCAST_ALLOWED_HOSTS=backcast.example.com uv run gunicorn --timeout 120 backcast.web.wsgi
```

Settings come from environment variables:

| Variable                    | Default                          | What it's for                                            |
| --------------------------- | -------------------------------- | -------------------------------------------------------- |
| `BACKCAST_SECRET_KEY`       | an insecure development key      | Django's secret key. Set it in production.               |
| `BACKCAST_ALLOWED_HOSTS`    | `localhost,127.0.0.1`            | Comma-separated host names.                              |
| `BACKCAST_DEBUG`            | off                              | Set to `1` for Django's debug pages.                     |
| `BACKCAST_CACHE_DIR`        | `backcast-cache` in the temp dir | Where results are cached.                                |
| `BACKCAST_STATIC_ROOT`      | unset                            | Only needed if you run `collectstatic`.                  |
| `BACKCAST_RUNS_PER_HOUR`    | `30`                             | New backtests one visitor may start per hour.            |
| `BACKCAST_CLIENT_IP_HEADER` | unset                            | Header your reverse proxy puts the visitor's address in. |
| `BACKCAST_LOG_LEVEL`        | `INFO`                           |                                                          |

Results are cached on disk for six hours per symbol and horizon, and every Gunicorn worker reads the same cache.
WhiteNoise serves the static files directly from the package, so `collectstatic` is optional.

Each worker starts its own pool of processes, one per core, the first time it runs a backtest. On a small machine,
one or two workers are plenty. Backtests run inside the request and can take more than ten seconds on a slow
server, which is why the example raises Gunicorn's 30-second default timeout.

Because an uncached run keeps every core busy, each visitor can start only a limited number per hour; cached
results don't count. Behind a reverse proxy, set `BACKCAST_CLIENT_IP_HEADER` (for example `X-Forwarded-For`), or
every visitor appears to come from the proxy and they all share one limit. Only set it if the proxy overwrites
that header, since anyone can send it.

## Limits

Read the results with these in mind:

- 30 windows is a small sample. A model that looks good this week can look average next week, and shifting the
  windows by a single day can turn a significant win into noise.
- The windows are one horizon apart, so the 1-day backtest only covers the last 30 trading days. The 20-day one
  covers roughly the last two and a half years.
- Only the price history goes in. There's no volume, no fundamentals, no news.
- Errors are measured on prices, not on trading returns. A model that beats naive here could still lose money once
  you add costs.
- Dividends aren't added back. On the day a stock goes ex-dividend its price drops by about the dividend, and none
  of the models forecast that.
- Future dates follow the exchange's holiday calendar for most major exchanges. For the rest, they skip weekends
  only.
- Prices come through [yfinance](https://github.com/ranaroussi/yfinance), an unofficial Yahoo Finance client. Yahoo's
  terms apply to the data.

None of this is investment advice.

## Planned

What I'm working on next. None of it exists yet.

- Far more forecasts per stock: one from every trading day over the last two years instead of 30, compared with
  Diebold–Mariano tests that account for the forecasts overlapping. This fixes the small-sample problem above.
- Hansen's test for superior predictive ability, which answers the question directly: does any model beat naive,
  once you allow for having tried several?
- The same backtest across the S&P 500, with the results published.
- A pretrained time-series foundation model (Chronos-Bolt), and a volatility track. Volatility, unlike price, is
  known to be partly predictable, so models there have a much better record against naive.

## Development

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

CI runs the same three commands on every pull request. It also checks the JavaScript for syntax errors, runs
Django's system checks, builds the wheel to make sure the templates and static files are still packaged, and
makes sure `requirements.txt` still matches `uv.lock`.

The tests use synthetic price series and never touch the network. They cover the walk-forward split (including a
model that reports how many prices it was shown, to catch any leakage), the metric arithmetic, the trading
calendars, and the views with the price download mocked out.

The backtest pool uses the `forkserver` start method, because forked workers deadlocked on OpenMP state left over
from the parent. As a result, a script that calls `walk_forward` has to live in a real file and keep its top-level
code under `if __name__ == "__main__":`. Piping it into `python -` won't work.

## Contributing

Fixes and new models are welcome. [CONTRIBUTING.md](CONTRIBUTING.md) covers setup, the checks CI runs, and what a
new model has to guarantee. Please report security problems privately, as described in [SECURITY.md](SECURITY.md).

## License

MIT. See [LICENSE](LICENSE).

The web app bundles a few things written by other people. [D3](https://d3js.org) and
[Observable Plot](https://observablehq.com/plot/) are under the ISC license. The IBM Plex fonts are under the SIL Open
Font License, and a copy of it sits next to the font files.
