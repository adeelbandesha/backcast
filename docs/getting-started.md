# Getting started

## What you need

- Python 3.13. If you use uv, it downloads Python for you.
- An internet connection, because prices come from Yahoo Finance by default. If you'd rather work offline or with
  your own data, see [Price sources](price-sources.md).
- A few CPU cores help. A backtest spreads its work across all of them and takes 10 to 20 seconds on a four-core
  laptop.

## Install

With [uv](https://docs.astral.sh/uv/), from a clone of the repository:

```bash
git clone https://github.com/adeelbandesha/backcast.git
cd backcast
uv sync
```

`uv sync` creates a virtual environment in `.venv` with the exact versions in `uv.lock`. Prefix commands with
`uv run` to use it, as the examples below do.

With pip, use any Python 3.13 environment. `requirements.txt` pins the same versions as `uv.lock`:

```bash
pip install -r requirements.txt
pip install -e .
```

After a pip install, drop the `uv run` prefix from the commands in these guides.

## Your first backtest

```bash
uv run backcast run SPY --horizon 5
```

This downloads five years of SPY's daily closes, forecasts the price five trading days ahead from each of the last
504 trading days, and compares every model with the naive forecast. You get a one-line verdict and a table with one
row per model. [The command line](command-line.md) explains each column, and
[Reading the results](reading-results.md) explains how to judge them.

The horizon can be 1, 5 or 20 trading days.

## The web app

```bash
uv run backcast web
```

Open http://127.0.0.1:8000, type a company name or ticker, and pick a horizon. The first run of a symbol takes a
few seconds; after that it loads straight from the cache. [The web app](web-app.md) walks through the pages.

To use another port:

```bash
uv run backcast web 127.0.0.1:8080
```

To reach it from other devices on your network, listen on every address and name the host you'll type in the
browser, because Backcast only answers to the host names in `BACKCAST_ALLOWED_HOSTS`:

```bash
BACKCAST_ALLOWED_HOSTS=192.168.1.20 uv run backcast web 0.0.0.0:8000
```

This is Django's development server, which is fine on your own machine. To put Backcast on the internet, follow
[Deployment](deployment.md) instead.

## Next steps

- Test your own price data: [Price sources](price-sources.md).
- Add a model and see whether it beats naive: [The models](models.md).
- Script backtests from Python or a notebook: [Python API](python.md).
