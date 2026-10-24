# Backcast documentation

Backcast asks one question of a stock's price history: could any forecasting model have predicted it better than
assuming tomorrow's price equals today's? These guides cover everything you can do with it.

## Using Backcast

- [Getting started](getting-started.md): install it, run your first backtest, start the web app.
- [The command line](command-line.md): every command and option, and what each column of the output means.
- [The web app](web-app.md): what each page and chart shows.
- [Reading the results](reading-results.md): how a backtest works, what the metrics measure, and how the
  significance tests decide whether a win is real.
- [The models](models.md): the six built-in models, and how to add your own.

## Data

- [Price sources](price-sources.md): Yahoo Finance is the default. This guide covers switching to your own CSV
  files or writing a small module for any other provider.

## Running it for other people

- [Configuration](configuration.md): every environment variable in one table.
- [Deployment](deployment.md): running the web app in production behind a reverse proxy.

## Using it from Python

- [Python API](python.md): load prices, run a backtest and work with the results as pandas objects.

If something here is wrong or unclear, please [open an issue](https://github.com/adeelbandesha/backcast/issues).
