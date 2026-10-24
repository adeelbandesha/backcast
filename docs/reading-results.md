# Reading the results

This guide explains what a backtest does, what each number measures, and how Backcast decides whether a model
really beat naive. The Method page in the web app covers the same ground more briefly.

## The question

The naive forecast says the close in `h` trading days will equal today's close. Daily stock prices behave a lot
like a random walk, and for a random walk naive is the best forecast there is. So a model that beats naive by more
than luck has found something real, and one that doesn't has found nothing, however close its forecast line looks
on a chart.

## How a backtest runs

1. **Load prices.** Five years of daily closes from the [price source](price-sources.md), adjusted for splits but
   not for dividends, finished trading days only.
2. **Pick forecast dates.** Each of the last 504 trading days, about two years, is a forecast origin. The first
   origin needs at least 500 earlier closes to learn from, and a backtest needs at least 100 origins, so a stock
   needs about 600 trading days of history at the 1-day horizon and a few more at longer ones.
3. **Forecast.** From each origin, every model sees the closes up to and including that day, never a later one, and
   forecasts the close `h` trading days ahead, together with nine quantiles from 10% to 90%.
4. **Refit every 20 days.** Models are re-estimated every 20 trading days and keep their parameters in between,
   while still seeing each new close, which is how a model is used in practice. Re-estimating every day instead
   was tested on AAPL, KO and NVDA at all three horizons: it moved each model's error relative to naive by 0.2
   percentage points on average and 1.3 at most, changed no verdict, and took 17 times as long.
   `scripts/refit_check.py` repeats that experiment.
5. **Score.** Each forecast is compared with the close that actually came, `h` days later.

Only the endpoint of each forecast is scored, not the path to it. Errors are measured on log prices, so a 2% miss
counts the same at $20 as at $2,000. The models also work on log prices and convert their forecasts back, which
keeps every forecast above zero and makes each model's point forecast the median of its distribution.

Why every trading day and not a handful of windows: an earlier version used 30 non-overlapping windows, and moving
them by a single day turned one stock's significant result into noise. With 504 origins the answer no longer depends
on where the windows happen to fall.

## The metrics

All of them are computed over the 504 forecasts.

**Mean error.** The average absolute difference between the log forecast and the log of the actual close. For
errors of a few percent this is very close to the percentage error, so 0.031 reads as "missed by about 3.1% on
average".

**Error relative to naive.** The model's mean error divided by naive's. 0.994 (or −0.6% on the web page) means 0.6%
less error than naive. The leaderboard is sorted by this.

**CRPS relative to naive.** The continuous ranked probability score judges the whole forecast distribution, not
only its centre. Backcast approximates it as twice the average pinball loss over the nine quantiles. For a forecast
with no spread it equals the absolute error. Quantiles beyond 10% and 90% aren't scored, so a miss far into the
tails costs no more than a near one here.

**Interval score relative to naive.** Gneiting and Raftery's score for the 80% interval: the interval's width, plus
ten times the distance by which the actual close lands outside it (2 divided by the 20% left outside). It rewards narrow intervals only when they still
hold the actual price.

**Days won.** The share of forecasts in which the model was closer than naive. When a model forecast exactly what
naive did, which ARIMA often does when it settles on a random walk, that day is a tie and counts as neither. The share
of ties is reported separately.

**Direction right.** How often the forecast pointed the same way as the actual move. Forecasts of no change are left
out. Most stocks rose more often than they fell in recent years, so the web page also shows how often always
predicting a rise would have been right. A model needs to beat that, not 50%.

**In 80% interval.** How often the actual close landed inside the model's 80% interval. Near 80% is well
calibrated. Much higher means the intervals are too wide, much lower that they're too narrow. Gaussian intervals
can land near 90%, as they did for SPY over these two years: a few very large days inflate the volatility they're
built from.

## Is the win real?

A model can beat naive over two years by chance. Backcast uses two tests to separate skill from luck.

**Diebold-Mariano, per model.** Forecasts made on neighbouring days overlap: two 20-day forecasts made a day apart
share 19 days of the future, so their errors move together. A plain t-test treats them as independent and finds
edges that aren't there; in a simulation with no real edge it called 20-day results significant two times out of
three. The Diebold-Mariano test allows for correlation over `h − 1` days and adds Harvey, Leybourne and Newbold's
small-sample correction. In the same simulation it was wrong 5 to 7% of the time at the 5% level, close to the 5%
it should be. The test is two-sided: a small p-value means the model differs from naive, so read it together with
the error relative to naive to see in which direction. Five models are tested against naive, so the p-values are
then adjusted with Holm's method to keep the chance of any false "significant" at 5%.

**Hansen's SPA test, for the headline.** The headline asks whether any of the five models beat naive. Picking the
best of five and testing it alone overstates the evidence, because the best of five is the one most likely to have
been lucky. The test for superior predictive ability bootstraps the daily error differences of all the models
together, resampling runs of neighbouring days to keep the overlap, and asks how often the best model would look
this good if none were better than naive. A result is called significant only when that p-value is below 0.05.

So the headline can read three ways:

| Headline                                        | Meaning                                                              |
| ----------------------------------------------- | -------------------------------------------------------------------- |
| "beat the naive forecast … statistically significant" | The best model had less error, and the SPA p-value is below 0.05. |
| "edged out the naive forecast … within the noise" | It had less error, but not by more than luck explains.              |
| "Nothing beat the naive forecast"               | No model had less error than naive.                                 |

## Trends

A significant result means a model did better over this particular period, not that it will keep doing so. Some
models forecast the same direction almost every time. Drift, for example, always extends the stock's average move,
so while a price keeps rising it beats naive day after day without forecasting anything, and in a falling market it
would lose just as steadily.

Two things guard against reading too much into that:

- **Up and down markets.** Each model's error relative to naive is split by whether the close was above or below
  its 200-day average on the day of the forecast. That is known when the forecast is made, so the split shows
  whether an edge held in both kinds of market.
- **The trend note.** When the best model predicted the same direction from at least 90% of the origins and the
  price moved that way over the period, the verdict says the win reflects the trend more than forecasting skill.

## Limits

- Two years is one stretch of market history. A result can hold over these two years and not the next two.
- Only the price history goes in: no volume, fundamentals, news or other stocks.
- Errors are on prices, not trading returns. There are no positions and no transaction costs, so a model that beats
  naive here could still lose money as a trading strategy.
- Dividends aren't added back. On an ex-dividend day the price drops by about the dividend, and no model here
  forecasts that.
- Recently listed stocks can't be tested until they have about two and a half years of history.

None of this is investment advice.
