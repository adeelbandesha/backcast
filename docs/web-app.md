# The web app

Start it with `uv run backcast web` and open http://127.0.0.1:8000. The app has four pages: the home page, a
backtest page for each symbol, Models and Method.

## Home

Type a ticker or a company name. Suggestions appear as you type; use the arrow keys and Enter, or click one. Pick a
horizon of 1, 5 or 20 trading days and press Run backtest. The example tickers under the form open a backtest
directly.

Suggestions come from the price source's symbol search. Yahoo Finance offers stocks, ETFs and indices. The CSV
source lists the files in its folder. A custom source without a search function shows no suggestions, but typing a
ticker still works. See [Price sources](price-sources.md).

## The backtest page

The address is `/backtest/SYMBOL/?h=5`, so you can bookmark or share a backtest. The tabs at the top switch the
horizon.

The first time a symbol and horizon are run, the page shows a progress bar for the 10 to 20 seconds the backtest
takes. Results are then cached for six hours, so the next visit, by you or anyone else, loads instantly.

The page has four parts.

**The verdict.** One sentence saying whether any model beat naive and whether the margin is more than luck,
followed by the dates the forecasts cover, how far the price moved over that period, and the p-value of Hansen's
SPA test. When the winning model predicted the same direction nearly every day of a period that moved that way,
a note says the win reflects the trend more than skill.

**The leaderboard.** One row per model, most accurate first, with the same measures as the
[command line](command-line.md#the-output) shows. Where the command line prints ratios, the page shows the change
from naive: −0.6% means 0.6% less error than naive. Blue marks a model that did better than naive and red one that
did worse. The best model is labelled under its name, an asterisk marks a Holm-adjusted p-value below 0.05, and the
notes under the table explain each column, including how often always predicting a rise would have called the
direction right.

**Up and down markets.** The same error relative to naive, split by whether the stock was above or below its
200-day average on the day each forecast was made. An edge that only appears in one kind of market is weaker
evidence than one that holds in both.

**Forecasts.** Two charts comparing one model with naive. Choose the model with the buttons above them; the best
model is selected at first.

- *The next few trading days* shows recent closes and each model's forecast from the latest close, with the chosen
  model's 80% interval as a shaded band. Future dates follow the exchange's holiday calendar when the price source
  knows it. When it doesn't, the dates skip weekends only and the chart says so.
- *Running total against naive* adds up, day by day, how much closer the chosen model was than naive. A line that
  climbs steadily is a lasting edge. A line that jumps once and then goes flat is one lucky stretch.

Hover over a chart to read exact values. Every chart has a Show the data link with the numbers behind it as a
table.

## Models and Method

Models describes each forecasting model and how to add one. Method explains the evaluation: rolling origins,
refits, scoring and the significance tests. [Reading the results](reading-results.md) covers the same ground.

## Limits on new backtests

An uncached backtest keeps every CPU core busy for several seconds, so each visitor can start 30 new ones per hour.
Cached results don't count. When you reach the limit, the page says so; symbols that have already run still load.
Whoever runs the server can change the limit with `BACKCAST_RUNS_PER_HOUR`.

## Theme and accessibility

The app follows your system's light or dark setting. The button in the top bar switches between them and remembers
your choice. Every page works with a keyboard, every chart has a text description and a data table, and the
backtest page still shows its results with JavaScript turned off, on a page of their own.
