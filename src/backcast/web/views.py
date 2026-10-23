"""Pages and the symbol search API. A backtest runs when its results partial is fetched, then stays cached."""

import math
import time
from urllib.parse import quote

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from ..data import DataError, load_prices, normalize_symbol, search_symbols
from ..evaluate import (
    HORIZONS,
    MIN_HISTORY,
    MIN_ORIGINS,
    ORIGINS,
    REFIT,
    SIGNIFICANCE,
    TREND_DAYS,
    headline,
    required_prices,
    run_backtest,
    trend_note,
)
from ..models import LEVEL, MODELS, MODELS_BY_KEY

DEFAULT_HORIZON = 5
RESULTS_TTL = 6 * 60 * 60
PRICES_TTL = 60 * 60
SEARCH_TTL = 60 * 60
EXAMPLES = ("AAPL", "MSFT", "NVDA", "SPY", "KO", "TSLA")


class RateLimited(Exception):
    """This client has started too many uncached backtests; the message is safe to show."""


def home(request):
    if request.GET.get("symbol", "").strip():
        try:
            symbol = normalize_symbol(request.GET["symbol"])
        except DataError as error:
            return _home(request, error=str(error), status=400)
        return redirect(_backtest_url(symbol, _horizon(request)))
    return _home(request)


def backtest(request, symbol):
    try:
        canonical = normalize_symbol(symbol)
    except DataError as error:
        return _home(request, error=str(error), status=404)
    horizon = _horizon(request)
    if canonical != symbol:
        return redirect(_backtest_url(canonical, horizon))
    return render(
        request,
        "backcast/backtest.html",
        {
            "symbol": symbol,
            "horizon": horizon,
            "horizons": HORIZONS,
            "origins": ORIGINS,
            "results_url": f"{reverse('backtest-results', args=[symbol])}?h={horizon}",
            # A cached run renders straight away; otherwise the page fetches it.
            "results": cache.get(_results_key(symbol, horizon)),
        },
    )


def backtest_results(request, symbol):
    try:
        symbol = normalize_symbol(symbol)
        results = _results(request, symbol, _horizon(request))
    except DataError as error:
        return render(request, "backcast/_results.html", {"error": str(error)}, status=422)
    except RateLimited as error:
        return render(request, "backcast/_results.html", {"error": str(error)}, status=429)
    return render(request, "backcast/_results.html", {"results": results})


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


def search(request):
    query = request.GET.get("q", "").strip()
    if not 1 <= len(query) <= 40:
        return JsonResponse({"error": "q must be 1 to 40 characters."}, status=400)
    key = f"search:{quote(query.lower())}"
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


def _results_key(symbol, horizon):
    return f"results:{quote(symbol)}:{horizon}"


def _results(request, symbol, horizon) -> dict:
    key = _results_key(symbol, horizon)
    if (results := cache.get(key)) is None:
        _count_new_run(request)
        prices = cache.get_or_set(f"prices:{quote(symbol)}", lambda: load_prices(symbol), PRICES_TTL)
        if len(prices) < required_prices(horizon):
            raise DataError(
                f"{symbol} has {len(prices)} days of history. A backtest needs at least {required_prices(horizon)}."
            )
        results = _summarize(run_backtest(prices, horizon))
        cache.set(key, results, RESULTS_TTL)
    return results


def _count_new_run(request):
    """An uncached run keeps every core busy for several seconds, so each client gets a few per hour."""
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


def _summarize(report) -> dict:
    """Plain values for the template and the charts, so the cache holds no pandas objects."""
    verdict = report.verdict
    rows = [
        {
            "key": row.model,
            "name": MODELS_BY_KEY[row.model].name,
            "family": MODELS_BY_KEY[row.model].family,
            "mae": f"{row.mae:.2%}",
            "change": _versus(row.model, row.relative_mae),
            "crps": _versus(row.model, row.relative_crps),
            "interval": _versus(row.model, row.relative_interval),
            "win_rate": _percent(row.win_rate),
            "direction": _percent(row.direction),
            "coverage": _percent(row.coverage),
            "p_value": _p_value(row.p_adjusted),
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
                "cells": [_versus(row["key"], report.trend.at[up, row["key"]]) for up in (True, False)],
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
                key: {"mean": _numbers(f.mean), "lower": _numbers(f.lower), "upper": _numbers(f.upper)}
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
        "trend": trend,
        "trend_days": TREND_DAYS,
        "spa_p_value": _p_value(verdict.p_value),
        "significance": SIGNIFICANCE,
        "level": LEVEL,
        "as_of": report.prices.index[-1].date(),
        "first_origin": results.origin.min().date(),
        "last_target": results.target.max().date(),
        "period_change": _signed_percent(verdict.period_change, digits=0),
        "origins": verdict.origins,
        "up_origins": verdict.up_origins,
        "always_up": _percent(verdict.up_origins / verdict.origins),
        "calendar_known": report.prices.attrs.get("calendar") is not None,
        "chart": chart,
    }


def _dates(index) -> list[str]:
    return [timestamp.strftime("%Y-%m-%d") for timestamp in index]


def _numbers(values) -> list[float | None]:
    # JSON has no NaN, and four decimals is plenty for prices.
    return [round(float(v), 4) if math.isfinite(v) else None for v in values]


def _percent(value) -> str:
    return "—" if math.isnan(value) else f"{value:.0%}"


def _signed_percent(value, digits=1) -> str:
    if round(value, digits + 2) == 0:
        return f"{0:.{digits}%}"
    return f"{value:+.{digits}%}".replace("-", "−")


def _versus(key, relative) -> dict:
    """A model's score relative to naive as a signed change, colored by whether it is better or worse."""
    if key == "naive" or math.isnan(relative):
        return {"text": "—", "tone": ""}
    return {"text": _signed_percent(relative - 1), "tone": _tone(relative - 1)}


def _tone(change) -> str:
    # Judged at the precision shown, so "0.0%" is never colored as a win or a loss.
    change = round(change, 3)
    return "better" if change < 0 else "worse" if change > 0 else ""


def _p_value(value) -> str:
    if math.isnan(value):
        return "—"
    return "< 0.001" if value < 0.001 else f"{value:.3f}"
