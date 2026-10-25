"""Pages and the symbol search API. Backtests run in the worker (see tasks.py); pages show saved runs."""

import functools
import json
import time
from pathlib import Path
from urllib.parse import quote

import pandas as pd
from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from ..data import DataError, normalize_symbol, search_symbols, source_name
from ..evaluate import HORIZONS, MIN_HISTORY, MIN_ORIGINS, ORIGINS, REFIT, SIGNIFICANCE, TREND_DAYS, required_prices
from ..models import LEVEL, MODELS, MODELS_BY_KEY
from ..study import findings as study_findings
from .models import STALLED_AFTER, Run
from .tasks import EXAMPLES, _p_value, _signed_percent, _versus, cached_prices, fingerprint, submit

DEFAULT_HORIZON = 5
SEARCH_TTL = 60 * 60
STUDY = Path(__file__).parent / "static/backcast/study"


class RateLimited(Exception):
    """This client has started too many new backtests; the message is safe to show."""


def home(request):
    if request.GET.get("symbol", "").strip():
        try:
            symbol = normalize_symbol(request.GET["symbol"])
        except DataError as error:
            return _home(request, error=str(error), status=400)
        return redirect(_backtest_url(symbol, _horizon(request)))
    return _home(request)


def backtest(request, symbol):
    """Redirects to the run for today's prices, starting it if it hasn't run yet."""
    try:
        canonical = normalize_symbol(symbol)
    except DataError as error:
        return _home(request, error=str(error), status=404)
    horizon = _horizon(request)
    if canonical != symbol:
        return redirect(_backtest_url(canonical, horizon))
    try:
        prices = cached_prices(symbol)
        if len(prices) < required_prices(horizon):
            raise DataError(
                f"{symbol} has {len(prices)} days of history. A backtest needs at least {required_prices(horizon)}."
            )
        run_id = fingerprint(prices, horizon)
        run = Run.objects.filter(pk=run_id).first()
        if run is None or run.failed:
            _count_new_run(request)
            run = submit(run_id, prices, horizon)
    except DataError as error:
        return _run_page(request, symbol, horizon, error=str(error), status=422)
    except RateLimited as error:
        return _run_page(request, symbol, horizon, error=str(error), status=429)
    return redirect(run)


def run(request, run_id):
    run = get_object_or_404(Run, pk=run_id)
    return _run_page(request, run.symbol, run.horizon, run=run)


def run_results(request, run_id):
    """What the run page polls: 202 and a progress line until the run is done or has failed."""
    run = get_object_or_404(Run, pk=run_id)
    if run.status == Run.DONE:
        return render(request, "backcast/_run.html", {"run": run})
    if run.failed:
        return render(request, "backcast/_results.html", {"error": run.message, "retry_url": _retry_url(run)})
    return HttpResponse(_progress(run), status=202, content_type="text/plain; charset=utf-8")


def models(request):
    return render(request, "backcast/models.html", {"models": MODELS, "level": LEVEL})


def method(request):
    return render(
        request,
        "backcast/method.html",
        {
            "origins": ORIGINS,
            "refit": REFIT,
            "min_history": MIN_HISTORY,
            "min_origins": MIN_ORIGINS,
            "trend_days": TREND_DAYS,
            "level": LEVEL,
            "significance": SIGNIFICANCE,
        },
    )


def findings(request):
    results = STUDY / "results.csv"
    context = _findings(results, results.stat().st_mtime_ns) if results.exists() else {}
    return render(
        request,
        "backcast/findings.html",
        {**context, "significance": SIGNIFICANCE, "level": LEVEL, "trend_days": TREND_DAYS},
    )


@functools.lru_cache(maxsize=1)
def _findings(results, version):
    """The study's dataset only changes when it is exported again, so it is read once per version of the file."""
    result = study_findings(pd.read_csv(results))
    for row in result["summary"]:
        for kind in ("real", "placebo"):
            row[kind]["percent"] = _share(row[kind]["share"])
        row["placebo"]["range"] = f"{_share(row['placebo']['low'])} to {_share(row['placebo']['high'])}"
    notes = json.loads((results.parent / "study.json").read_text())
    return {
        "notes": notes,
        "untested": len(notes["study"]["excluded"]) + len(notes["study"]["not_loaded"]),
        "summary": result["summary"],
        "horizons": result["horizons"],
        "models": [
            {
                "name": model.name,
                "cells": [{k: _share(v) for k, v in cell.items()} for cell in result["models"][model.key]],
            }
            for model in MODELS
            if model.key in result["models"]
        ],
        "winners": [
            {
                **row,
                "best": MODELS_BY_KEY[row["best"]].name,
                "change": _versus(row["best"], row["relative_mae"]),
                "up": _versus(row["best"], row["relative_mae_up"]),
                "down": _versus(row["best"], row["relative_mae_down"]),
                "spa_p_value": _p_value(row["spa_p_value"]),
                "period_change": _signed_percent(row["period_change"], 0),
            }
            for row in result["winners"]
        ],
        "chart": {"p_values": result["p_values"]},
    }


def _share(value) -> str:
    return f"{value:.1%}"


def search(request):
    query = request.GET.get("q", "").strip()
    if not 1 <= len(query) <= 40:
        return JsonResponse({"error": "q must be 1 to 40 characters."}, status=400)
    key = f"search:{source_name()}:{quote(query.lower())}"
    if (results := cache.get(key)) is None:
        results = search_symbols(query)
        if results:  # an empty list may be a provider hiccup, so don't keep it
            cache.set(key, results, SEARCH_TTL)
    return JsonResponse({"results": results})


def _home(request, error=None, status=200):
    return render(
        request,
        "backcast/home.html",
        {
            "error": error,
            "symbol": request.GET.get("symbol", "").strip()[:20],
            "horizon": _horizon(request),
            "horizons": HORIZONS,
            "examples": EXAMPLES,
            "origins": ORIGINS,
        },
        status=status,
    )


def _horizon(request) -> int:
    value = request.GET.get("h", "")
    return int(value) if value.isdigit() and int(value) in HORIZONS else DEFAULT_HORIZON


def _backtest_url(symbol, horizon):
    return f"{reverse('backtest', args=[symbol])}?h={horizon}"


def _retry_url(run):
    return _backtest_url(run.symbol, run.horizon)


def _run_page(request, symbol, horizon, run=None, error=None, status=200):
    if run and run.failed:
        error = run.message
    return render(
        request,
        "backcast/backtest.html",
        {
            "symbol": symbol,
            "horizon": horizon,
            "horizons": HORIZONS,
            "run": run,
            "error": error,
            "retry_url": run and _retry_url(run),
            "pending": run and not error and run.status != Run.DONE,
            "progress": run and _progress(run),
        },
        status=status,
    )


def _progress(run) -> str:
    if run.status == Run.RUNNING:
        return f"Forecasting from {ORIGINS} trading days with six models. This takes 10 to 30 seconds."
    ahead = Run.objects.filter(
        status=Run.QUEUED, updated__lt=run.updated, updated__gte=timezone.now() - STALLED_AFTER
    ).count()
    if ahead:
        return f"Waiting in the queue behind {ahead} other run{'s' if ahead > 1 else ''}."
    return "Waiting for the worker to start this run."


def _count_new_run(request):
    """A new run keeps every core busy for several seconds, so each client gets a few per hour."""
    limit = settings.BACKCAST_RUNS_PER_HOUR
    key = f"runs:{quote(_client_ip(request))}:{int(time.time() // 3600)}"
    cache.add(key, 0, 60 * 60)
    try:
        count = cache.incr(key)
    except ValueError:  # the key expired between add and incr
        count = 1
        cache.set(key, count, 60 * 60)
    if count > limit:
        raise RateLimited(
            f"That's more than {limit} new backtests from you this hour, which is the limit. "
            "Symbols that have already run still load. Try new ones again a little later."
        )


def _client_ip(request) -> str:
    # Only trust a forwarding header the deployment says its proxy sets; anyone can send one otherwise.
    if header := settings.BACKCAST_CLIENT_IP_HEADER:
        if forwarded := request.headers.get(header):
            return forwarded.split(",")[-1].strip()  # the entry the proxy itself appended
    return request.META.get("REMOTE_ADDR", "")
