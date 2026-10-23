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

## Adding a model

The README explains the mechanics: subclass `Model`, return a `Forecast`, add an instance to `MODELS`.

A model only ever receives the prices up to the forecast date, and it must not reach for anything else, such as data
downloaded inside `forecast`, that could carry information from after that date. The leakage test in
`tests/test_evaluate.py` shows the property every model has to keep.

A model doesn't have to beat naive to be worth adding. Finding out is what the benchmark is for.

## Reporting a bug

Say which symbol and horizon you ran, what you expected, and what you got. A screenshot or the CLI output helps.
Prices come from Yahoo Finance, and now and then they're wrong at the source, so if a number looks off, check it
against another price source first.
