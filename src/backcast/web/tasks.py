"""Backtests run here, in the Huey worker, and each one is saved as a Run that a permalink can show later."""

import hashlib
import json
import logging
import math
from urllib.parse import quote

from django.core.cache import cache as django_cache
from django.template.loader import render_to_string
from huey import crontab
from huey.contrib.djhuey import db_periodic_task, db_task

from ..data import YEARS, DataError, load_prices, source_name
from ..evaluate import (
    HORIZONS,
    MIN_HISTORY,
    ORIGINS,
    REFIT,
    SIGNIFICANCE,
    TREND_DAYS,
    headline,
    run_backtest,
    trend_note,
)
from ..models import LEVEL, MODELS, MODELS_BY_KEY
from ..version import code_version
from .formats import p_value, percent, signed_percent, versus
from .models import Run

PRICES_TTL = 60 * 60
EXAMPLES = ("AAPL", "MSFT", "NVDA", "SPY", "KO", "TSLA")
log = logging.getLogger(__name__)


def cached_prices(symbol: str, fresh: bool = False):
    key = f"prices:{source_name()}:{quote(symbol)}"
    prices = None if fresh else django_cache.get(key)
    if prices is None:
        prices = load_prices(symbol)
        django_cache.set(key, prices, PRICES_TTL)
    return prices


def run_settings(horizon: int) -> dict:
    return {
        "horizon": horizon,
        "origins": ORIGINS,
        "refit": REFIT,
        "min_history": MIN_HISTORY,
        "years": YEARS,
        "level": LEVEL,
        "models": [model.key for model in MODELS],
    }


def fingerprint(prices, horizon: int) -> str:
    """The run id: a hash of every input that can change the results."""
    digest = hashlib.sha256()
    inputs = {
        "symbol": prices.name,
        "calendar": prices.attrs.get("calendar"),
        "source": source_name(),
        "settings": run_settings(horizon),
        "code": code_version(),
    }
    digest.update(json.dumps(inputs, sort_keys=True).encode())
    digest.update(prices.index.as_unit("s").asi8.tobytes())
    digest.update(prices.to_numpy(dtype=float).tobytes())
    return digest.hexdigest()[:16]


def submit(run_id: str, prices, horizon: int) -> Run:
    """Save the run as queued and hand it to the worker. Callers check first that it isn't saved or running."""
    run, _ = Run.objects.update_or_create(
        pk=run_id,
        defaults={"status": Run.QUEUED, "error": ""},
        create_defaults={
            "symbol": prices.name,
            "horizon": horizon,
            "source": source_name(),
            "data_date": prices.index[-1].date(),
            "code_version": code_version(),
            "settings": run_settings(horizon),
        },
    )
    execute(run_id, prices)
    return run


@db_task()
def execute(run_id: str, prices):
    run = Run.objects.get(pk=run_id)
    if run.status == Run.DONE:  # queued twice, by two visitors at once
        return
    run.status = Run.RUNNING
    run.save(update_fields=["status", "updated"])
    try:
        results = _summarize(run_backtest(prices, run.horizon))
        run.html = render_to_string("backcast/_results.html", {"results": results})
        run.status = Run.DONE
    except (DataError, ValueError) as error:  # their messages are written for people
        run.status, run.error = Run.FAILED, str(error)
    except Exception:
        log.exception("Run %s (%s, %s days) failed", run_id, run.symbol, run.horizon)
        run.status, run.error = Run.FAILED, "The backtest failed on the server. Try again later."
    run.save()


# Weekdays at 22:30 UTC, after the US close in both summer (20:00 UTC) and winter (21:00 UTC).
@db_periodic_task(crontab(minute="30", hour="22", day_of_week="1-5"))
def precompute():
    """Queue the home page's examples at every horizon, so they're ready before anyone asks."""
    for symbol in EXAMPLES:
        try:
            prices = cached_prices(symbol, fresh=True)
        except DataError as error:
            log.warning("Skipped %s in the nightly run: %s", symbol, error)
            continue
        for horizon in HORIZONS:
            run_id = fingerprint(prices, horizon)
            run = Run.objects.filter(pk=run_id).first()
            if run is None or run.failed:
                submit(run_id, prices, horizon)


def _summarize(report) -> dict:
    """Everything _results.html and its charts need, as plain values."""
    verdict = report.verdict
    rows = [
        {
            "key": row.model,
            "name": MODELS_BY_KEY[row.model].name,
            "family": MODELS_BY_KEY[row.model].family,
            "mae": f"{row.mae:.2%}",
            "change": versus(row.model, row.relative_mae),
            "crps": versus(row.model, row.relative_crps),
            "interval": versus(row.model, row.relative_interval),
            "win_rate": percent(row.win_rate),
            "ties": f"{row.tie_rate:.0%} tied" if row.tie_rate >= 0.005 else "",
            "direction": percent(row.direction),
            "coverage": percent(row.coverage),
            "p_value": p_value(row.p_adjusted),
            "significant": row.p_adjusted < SIGNIFICANCE,
            "best": row.model == verdict.best,
        }
        for row in report.board.itertuples()
    ]
    results = report.results
    trend_counts = results[results.model == "naive"].uptrend.value_counts()
    trend = {
        "days": [int(trend_counts.get(True, 0)), int(trend_counts.get(False, 0))],
        "rows": [
            {
                "name": row["name"],
                "cells": [versus(row["key"], report.trend.at[up, row["key"]]) for up in (True, False)],
            }
            for row in rows
            if row["key"] != "naive"
        ],
    }
    # Enough history that the forecast fan stays readable: 60 days, or 8 horizons for long ones.
    shown = report.prices.iloc[-max(60, 8 * report.horizon) :]
    chart = {
        "horizon": report.horizon,
        "best": verdict.best,
        "models": [{"key": row["key"], "name": row["name"]} for row in rows],
        "history": {"dates": _dates(shown.index), "close": _numbers(shown)},
        "future": {
            "dates": _dates(report.future_dates),
            "paths": {
                key: {"median": _numbers(f.median), "lower": _numbers(f.lower), "upper": _numbers(f.upper)}
                for key, f in report.future.items()
            },
        },
        "origins": {
            key: {
                "origin": _dates(group.origin),
                "target": _dates(group.target),
                **{column: _numbers(group[column]) for column in ("actual", "predicted")},
                "error": [round(float(v), 6) for v in group.error],
            }
            for key, group in results.groupby("model", sort=False)
        },
    }
    return {
        "symbol": report.prices.name,
        "horizon": report.horizon,
        "headline": headline(verdict),
        "trend_note": trend_note(verdict),
        "verdict": verdict,
        "rows": rows,
        "rivals": len(rows) - 1,
        "trend": trend,
        "trend_days": TREND_DAYS,
        "spa_p_value": p_value(verdict.p_value),
        "significance": SIGNIFICANCE,
        "level": LEVEL,
        "as_of": report.prices.index[-1].date(),
        "first_origin": results.origin.min().date(),
        "last_target": results.target.max().date(),
        "period_change": signed_percent(verdict.period_change, digits=0),
        "origins": verdict.origins,
        "up_origins": verdict.up_origins,
        "always_up": percent(verdict.up_origins / verdict.origins),
        "calendar_known": report.prices.attrs.get("calendar") is not None,
        "chart": chart,
    }


def _dates(index) -> list[str]:
    return [timestamp.strftime("%Y-%m-%d") for timestamp in index]


def _numbers(values) -> list[float | None]:
    # JSON has no NaN, and four decimals is plenty for prices.
    return [round(float(v), 4) if math.isfinite(v) else None for v in values]
