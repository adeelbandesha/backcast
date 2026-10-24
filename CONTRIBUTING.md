# Contributing

Bug reports, fixes and new models are all welcome.

## Setting up

You need [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pytest
```

The tests take a few seconds and don't need a network connection.

## Before you open a pull request

Run the same checks CI runs:

```bash
uv run ruff format .
uv run ruff check .
uv run pytest
```

CI also checks the JavaScript for syntax errors, runs Django's system checks, builds the wheel, and makes sure
`requirements.txt` still matches `uv.lock`. If you add or remove a dependency, do it with `uv add` or `uv remove`,
then regenerate the requirements file:

```bash
uv export --format requirements-txt --no-dev --no-emit-project --no-hashes --no-header --no-annotate --locked -o requirements.txt
```

Keep each pull request to one change. A fix with a test that fails without it is the easiest kind to review.

## What the tests cover

The tests use synthetic price series and never touch the network. They cover the walk-forward split (including
models that report how many prices they were shown and fitted on, to catch any leakage), the refit schedule, the
metric arithmetic, the false-positive rate of the Diebold-Mariano test on overlapping forecasts, the trading
calendars, the price source checks and the CSV source, and the views with the price download mocked out.

## The refit experiment

`scripts/refit_check.py` re-runs the experiment behind the refit interval: it backtests AAPL, KO and NVDA with models
re-estimated every day, every 5 days and every 20, and reports how far the results move. It takes about half an hour
on four cores. Pass other symbols as arguments to run it on those instead.

## Scripts that run backtests

The backtest pool uses the `forkserver` start method, because forked workers deadlocked on OpenMP state left over
from the parent. As a result, a script that calls `walk_forward` has to live in a real file and keep its top-level
code under `if __name__ == "__main__":`. Piping it into `python -` won't work.

## Adding a model

[The models](docs/models.md) explains the mechanics: subclass `Model`, return a `Forecast`, add an instance to
`MODELS`.

A model only ever receives the prices up to the forecast date, and it must not reach for anything else, such as data
downloaded inside `forecast`, that could carry information from after that date. The leakage test in
`tests/test_evaluate.py` shows the property every model has to keep.

Its forecast and quantiles must be positive prices, with the quantiles in increasing order; the backtest stops
with an error otherwise. Fitting on log prices and converting back, as the built-in models do, guarantees both.

A model doesn't have to beat naive to be worth adding. Finding out is what the benchmark is for.

## Reporting a bug

Say which symbol and horizon you ran, what you expected, and what you got. A screenshot or the CLI output helps.
Say which price source you used, too. Yahoo Finance prices are now and then wrong at the source, so if a number looks
off, check it against another source first.
