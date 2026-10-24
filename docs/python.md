# Python API

Everything the command line and the web app do is available from Python, with the results as pandas objects you can
analyse further.

## A first backtest

```python
from backcast.data import load_prices
from backcast.evaluate import headline, run_backtest

if __name__ == "__main__":
    prices = load_prices("SPY")
    report = run_backtest(prices, 5)
    print(headline(report.verdict))
    print(report.board)
```

Keep the code that runs a backtest under `if __name__ == "__main__":` in a script. The backtest runs in worker
processes started with the `forkserver` method, which import your script again; without the guard, each worker would
start a backtest of its own. For the same reason, running a backtest from code piped into `python -` fails. In a
notebook the guard isn't needed.

## Loading prices

```python
load_prices(symbol, years=5) -> pandas.Series
```

Loads daily closes from the configured [price source](price-sources.md) and applies its checks: one positive close
per date, oldest first, split-adjusted and not dividend-adjusted, at most the last `years` years. The series is
named after the symbol, and `prices.attrs["calendar"]` holds the trading calendar, or `None` when it isn't known.
It raises `backcast.data.DataError` when the prices can't be loaded.

`load_prices` doesn't change the symbol, so pass it the way the source expects it, usually upper case.
`backcast.data.normalize_symbol` trims and upper-cases a symbol and rejects anything that isn't a valid ticker, as the
command line and the web app do.

You can also backtest a series you built yourself. It needs a `DatetimeIndex`, one positive close per trading day,
oldest first, and at least 604 closes for a 5-day horizon (`backcast.evaluate.required_prices(h)` gives the number
for any horizon).

## Running a backtest

```python
run_backtest(prices, horizon, models=MODELS) -> Report
```

`horizon` is the number of trading days ahead. The web app and command line offer 1, 5 and 20, but any positive
whole number works here. `models` defaults to the six built-in models; pass your own list to add or drop some. It
must include `Naive()`, which every other model is measured against:

```python
from backcast.models import MODELS

report = run_backtest(prices, 5, models=[*MODELS, Average()])
```

Models defined in your script work in the worker processes. In a notebook, define them in a `.py` module and import
them, because the workers can't import classes defined in notebook cells. [The models](models.md) explains how to
write one.

## The report

`Report` holds everything a backtest produced:

| Field          | Type                    | Contents                                                                  |
| -------------- | ----------------------- | ------------------------------------------------------------------------- |
| `prices`       | Series                  | The closes the backtest used.                                             |
| `horizon`      | int                     | Trading days ahead.                                                       |
| `results`      | DataFrame               | Every forecast that was scored, one row per model per forecast date.      |
| `board`        | DataFrame               | The leaderboard, one row per model, most accurate first.                  |
| `trend`        | DataFrame               | Error relative to naive in uptrends (`True`) and downtrends (`False`), one column per model. |
| `verdict`      | Verdict                 | The headline result.                                                      |
| `future_dates` | DatetimeIndex           | The next `horizon` trading days after the last close.                     |
| `future`       | dict of model key to Forecast | Each model's forecast from the last close.                          |

### results

| Column      | Meaning                                                                         |
| ----------- | ------------------------------------------------------------------------------- |
| `model`     | The model's key: `naive`, `drift`, `ets`, `arima`, `theta` or `lightgbm`.       |
| `origin`    | The date the forecast was made; the model saw closes up to and including it.    |
| `target`    | The date being forecast, `horizon` trading days later.                          |
| `last`      | The close on the origin date.                                                   |
| `actual`    | The close on the target date.                                                   |
| `predicted` | The point forecast (the median).                                                |
| `lower`, `upper` | The 80% interval.                                                          |
| `error`     | Absolute error of the log forecast.                                             |
| `crps`      | CRPS of the nine quantiles, on log prices.                                      |
| `interval`  | Interval score of the 80% interval, on log prices.                              |
| `uptrend`   | Whether the origin's close was above its 200-day average.                       |

For example, the share of days each model was within 2% of the actual close:

```python
(report.results.error < 0.02).groupby(report.results.model).mean()
```

### board

The columns described in [The command line](command-line.md#the-output), with `mae` as a fraction (0.031 rather than
3.1). `win_rate`, `tie_rate`, `p_value`, `direction` and `p_adjusted` are `NaN` for naive.

### verdict

| Field              | Meaning                                                                             |
| ------------------ | ----------------------------------------------------------------------------------- |
| `best`             | Key of the most accurate model other than naive.                                    |
| `beaten`           | Whether that model had less error than naive.                                       |
| `significant`      | Whether it did and the SPA p-value is below 0.05.                                   |
| `relative_change`  | The best model's error relative to naive, minus 1: −0.012 means 1.2% less error.    |
| `p_value`          | Hansen's SPA p-value for "no model beats naive".                                    |
| `origins`          | Number of forecast dates.                                                           |
| `up_origins`       | How many of them were followed by a rise over the horizon.                          |
| `period_change`    | Price change from the first forecast date to the last target date.                  |
| `one_way`          | `"rise"` or `"fall"` when the best model forecast the same direction from at least 90% of origins, otherwise `None`. |
| `one_way_origins`  | How many origins that was.                                                          |

`headline(verdict)` and `trend_note(verdict)` turn it into the sentences the command line and the web app print.

### Forecast

Each value in `report.future` is a `Forecast` with `median` (an array of `horizon` prices), `quantiles` (shape
`(9, horizon)`, from 10% to 90%) and the shortcuts `lower` and `upper` for the 80% interval.

## Lower-level functions

`run_backtest` combines these, all in `backcast.evaluate`, which you can call on their own:

| Function                                        | Returns                                                     |
| ----------------------------------------------- | ----------------------------------------------------------- |
| `walk_forward(prices, horizon, models, origins=504, refit=20)` | The `results` table. Change `origins` or `refit` to experiment with the design. |
| `leaderboard(results, horizon)`                 | The `board` table.                                          |
| `superior_predictive_ability(results, horizon)` | The SPA p-value.                                            |
| `by_trend(results)`                             | The `trend` table.                                          |
| `verdict(board, results, horizon)`              | The `Verdict`.                                              |
| `future_paths(prices, horizon, models)`         | `future_dates` and `future`.                                |

`scripts/refit_check.py` in the repository uses `walk_forward` this way to compare refit intervals.
