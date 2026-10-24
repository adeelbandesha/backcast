# The models

Every backtest runs the same six models. Each one turns a price history into a forecast path for the next `h`
trading days with nine quantiles from 10% to 90%; the outer two make the 80% interval.

| Model    | Kind             | How it forecasts                                                                              |
| -------- | ---------------- | --------------------------------------------------------------------------------------------- |
| Naive    | Baseline         | The price in `h` days equals today's. The benchmark every other model is compared with.       |
| Drift    | Baseline         | Extends the average daily percentage change over the training history.                        |
| ETS      | Statistical      | Exponential smoothing, with the error and trend components chosen automatically.              |
| ARIMA    | Statistical      | Auto-ARIMA, with the orders chosen by information criterion.                                  |
| Theta    | Statistical      | The Theta method, a strong performer in the M3 forecasting competition.                       |
| LightGBM | Machine learning | Gradient-boosted trees on the last 20 daily log returns, one quantile regression per quantile. |

ETS, ARIMA and Theta come from [statsforecast](https://github.com/Nixtla/statsforecast). LightGBM predicts the
log return over the whole horizon and interpolates the path between today and day `h`.

All six are fitted to log prices and convert their forecasts back to prices. That keeps every forecast above zero,
and since a quantile stays a quantile through the conversion, each model's point forecast is the median of its
distribution.

Naive and Drift build their intervals from the volatility of daily log returns, assuming they are normally
distributed. Stock returns have fatter tails than that, which is why those intervals usually hold more than 80% of
actual prices.

## Adding a model

A model is a subclass of `Model` in `src/backcast/models.py`. Give it a `key`, a `name`, a `family` and a one-line
`summary`, implement `forecast(y, h)`, and append an instance to `MODELS`. The command line, the leaderboard, the
significance tests and the charts all pick it up.

`forecast(y, h)` receives the closes up to the forecast date as a NumPy array, oldest first, and returns a
`Forecast` with:

- `median`: the point forecast, an array of `h` prices;
- `quantiles`: an array of shape `(9, h)`, one row per quantile in `QUANTILES` (10%, 20%, … 90%).

A model that forecasts the average log price of its whole history, as an example:

```python
class Average(Model):
    key, name, family = "average", "Historical average", "Baseline"
    summary = "Forecasts the average log price of the whole history."

    def forecast(self, y, h):
        log_y = np.log(y)
        median = np.full(h, np.exp(log_y.mean()))
        return Forecast(median, median * np.exp(Z * log_y.std(ddof=1)))
```

`Z` holds the standard normal quantiles that match `QUANTILES`, one row each, so `median * np.exp(Z * spread)` gives
all nine rows at once.

### What every model must guarantee

- **Only the past.** A model sees nothing after the forecast date, and it must not reach for anything that could,
  such as data downloaded inside `forecast`. The leakage test in `tests/test_evaluate.py` shows the property every
  model has to keep.
- **Valid forecasts.** The median and every quantile must be positive, finite prices, with the quantiles in
  increasing order. The backtest stops with an error naming the model and the date otherwise. Working on log prices,
  as the built-in models do, guarantees this.
- **Picklable.** Forecasts run in worker processes, so the model is pickled. Defining the class at module level is
  enough.

### Slow models

Backtests call a model about 500 times. If estimating is slow, implement `fit(y, h)` instead of `forecast`. It
estimates on `y` and returns a function that forecasts from any later history using the same estimates:

```python
def fit(self, y, h):
    params = estimate(np.log(y))

    def forecast(latest):
        return forecast_from(params, np.log(latest), h)

    return forecast
```

The backtest calls `fit` every 20 trading days and the returned function on each day in between, passing the longer
history each time. The statsforecast and LightGBM models in `models.py` work this way.

A model doesn't have to beat naive to be worth adding. Finding out is what Backcast is for.
