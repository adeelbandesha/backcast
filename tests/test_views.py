import json
import re
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest
from django.core.cache import cache
from django.utils import timezone

from backcast.data import DataError
from backcast.evaluate import run_backtest
from backcast.models import Drift, Naive
from backcast.web import tasks
from backcast.web.models import Run

FAST_MODELS = [Naive(), Drift()]


@pytest.fixture(autouse=True)
def setup(settings, db):
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    cache.clear()


@pytest.fixture
def prices(monkeypatch):
    """Fake five years of prices, and a fast backtest with two cheap models. Returns the load_prices call log."""
    values = 100 * np.exp(np.cumsum(np.random.default_rng(0).normal(0, 0.01, 1250)))
    calls = []

    def load_prices(symbol):
        calls.append(symbol)
        return pd.Series(values, index=pd.bdate_range(end="2026-10-23", periods=len(values)), name=symbol)

    monkeypatch.setattr(tasks, "load_prices", load_prices)
    monkeypatch.setattr(tasks, "run_backtest", lambda p, h: run_backtest(p, h, FAST_MODELS))
    return calls


def chart_data(response) -> dict:
    payload = re.search(r'<script id="chart-data" type="application/json">(.*?)</script>', response.text, re.S)

    def reject(constant):
        raise ValueError(f"{constant} is not valid JSON")

    return json.loads(payload.group(1), parse_constant=reject)


def results_page(client, symbol, **params):
    response = client.get(f"/backtest/{symbol}/", params)
    assert response.status_code == 302 and response["Location"].startswith("/runs/")
    return client.get(response["Location"])


def test_home(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Can any model beat the naive forecast?" in response.text


def test_home_redirects_to_the_backtest(client):
    response = client.get("/", {"symbol": " aapl ", "h": "20"})
    assert response.status_code == 302
    assert response["Location"] == "/backtest/AAPL/?h=20"


def test_home_rejects_an_invalid_symbol(client):
    response = client.get("/", {"symbol": "$$$"})
    assert response.status_code == 400
    assert "is not a valid ticker symbol" in response.text


def test_backtest_url_is_canonical(client):
    response = client.get("/backtest/msft/", {"h": "1"})
    assert response["Location"] == "/backtest/MSFT/?h=1"


def test_backtest_runs_and_redirects_to_the_saved_run(client, prices):
    response = results_page(client, "MSFT", h="5")
    assert response.status_code == 200
    assert "Leaderboard" in response.text and "data-src" not in response.text
    data = chart_data(response)
    assert [model["key"] for model in data["models"]] in (["drift", "naive"], ["naive", "drift"])
    assert len(data["origins"]["drift"]["origin"]) == 504
    assert "Up and down markets" in response.text
    assert len(data["future"]["dates"]) == 5
    assert len(data["history"]["close"]) == 60

    run = Run.objects.get()
    assert (run.symbol, run.horizon, run.status, run.source) == ("MSFT", 5, Run.DONE, "yahoo")
    assert run.data_date.isoformat() == "2026-10-23"
    assert run.settings["origins"] == 504 and run.code_version.startswith("0.1.0")
    assert "About this run" in response.text and run.id in response.text

    again = client.get("/backtest/MSFT/", {"h": "5"})
    assert again["Location"] == f"/runs/{run.id}/"
    assert prices == ["MSFT"] and Run.objects.count() == 1


def test_run_id_fingerprints_the_inputs(prices, monkeypatch):
    series = tasks.load_prices("MSFT")
    assert tasks.fingerprint(series, 5) == tasks.fingerprint(series.copy(), 5)
    assert tasks.fingerprint(series, 5) != tasks.fingerprint(series, 20)
    assert tasks.fingerprint(series, 5) != tasks.fingerprint(series.rename("AAPL"), 5)
    changed = series.copy()
    changed.iloc[100] += 0.01
    assert tasks.fingerprint(series, 5) != tasks.fingerprint(changed, 5)
    before = tasks.fingerprint(series, 5)
    monkeypatch.setenv("BACKCAST_PRICE_SOURCE", "csv")
    assert tasks.fingerprint(series, 5) != before


def test_a_queued_run_polls_until_it_is_done(client, prices):
    series = tasks.load_prices("KO")
    run = Run.objects.create(
        id="0123456789abcdef", symbol="KO", horizon=5, source="yahoo", data_date=series.index[-1], settings={}
    )
    page = client.get(f"/runs/{run.id}/")
    assert 'data-src="/runs/0123456789abcdef/results/"' in page.text
    assert "Waiting for the worker" in page.text

    pending = client.get(f"/runs/{run.id}/results/")
    assert pending.status_code == 202 and "Waiting for the worker" in pending.text

    tasks.execute(run.id, series)
    done = client.get(f"/runs/{run.id}/results/")
    assert done.status_code == 200
    assert "Leaderboard" in done.text and "About this run" in done.text


def test_unknown_runs_are_not_found(client):
    assert client.get("/runs/ffffffffffffffff/").status_code == 404
    assert client.get("/runs/ffffffffffffffff/results/").status_code == 404


def test_missing_prices_are_explained(client, monkeypatch):
    def missing(symbol):
        raise DataError(f"No price history found for {symbol}.")

    monkeypatch.setattr(tasks, "load_prices", missing)
    response = client.get("/backtest/NOPE/")
    assert response.status_code == 422
    assert "No price history found for NOPE." in response.text
    assert not Run.objects.exists()


def test_backtests_need_enough_history(client, monkeypatch):
    short = pd.Series(np.linspace(10, 20, 300), index=pd.bdate_range("2025-01-01", periods=300), name="NEW")
    monkeypatch.setattr(tasks, "load_prices", lambda symbol: short)
    response = client.get("/backtest/NEW/")
    assert response.status_code == 422
    assert "A backtest needs at least 604." in response.text


def test_a_failed_run_says_why_and_runs_again(client, prices, monkeypatch):
    def broken(p, h):
        raise ValueError("ETS returned a forecast that isn't finite.")

    monkeypatch.setattr(tasks, "run_backtest", broken)
    page = results_page(client, "MSFT")
    assert "ETS returned a forecast that isn&#x27;t finite." in page.text
    assert 'href="/backtest/MSFT/?h=5">Run it again' in page.text
    assert Run.objects.get().status == Run.FAILED

    monkeypatch.setattr(tasks, "run_backtest", lambda p, h: run_backtest(p, h, FAST_MODELS))
    assert "Leaderboard" in results_page(client, "MSFT").text


def test_unexpected_errors_are_logged_not_shown(client, prices, monkeypatch, caplog):
    def broken(p, h):
        raise RuntimeError("secret internals")

    monkeypatch.setattr(tasks, "run_backtest", broken)
    page = results_page(client, "MSFT")
    assert "failed on the server" in page.text and "secret internals" not in page.text
    assert "secret internals" in caplog.text


def test_a_stalled_run_is_started_again(client, prices):
    series = tasks.load_prices("MSFT")
    run_id = tasks.fingerprint(series, 5)
    Run.objects.create(id=run_id, symbol="MSFT", horizon=5, source="yahoo", data_date=series.index[-1], settings={})
    Run.objects.filter(pk=run_id).update(status=Run.RUNNING, updated=timezone.now() - timedelta(hours=1))
    assert "stopped before it finished" in client.get(f"/runs/{run_id}/").text
    assert "Leaderboard" in results_page(client, "MSFT").text


def test_nightly_precompute_saves_the_examples(client, prices, monkeypatch):
    monkeypatch.setattr(tasks, "EXAMPLES", ("SPY",))
    tasks.precompute.call_local()
    assert sorted(Run.objects.values_list("horizon", "status")) == [(1, "done"), (5, "done"), (20, "done")]
    results_page(client, "SPY", h="20")
    assert prices == ["SPY"]  # the precompute's prices were cached, so the page found its run


@pytest.mark.parametrize("query", ["", "   ", "x" * 41])
def test_search_validates_the_query(client, query):
    assert client.get("/api/search/", {"q": query}).status_code == 400


def test_search_does_not_cache_empty_results(client, monkeypatch):
    answers = iter([[], [{"symbol": "AAPL", "name": "Apple Inc.", "exchange": "NASDAQ"}]])
    monkeypatch.setattr("backcast.web.views.search_symbols", lambda query: next(answers))
    assert client.get("/api/search/", {"q": "apple"}).json() == {"results": []}
    assert client.get("/api/search/", {"q": "apple"}).json()["results"][0]["symbol"] == "AAPL"


def test_static_pages(client):
    assert "LightGBM" in client.get("/models/").text
    assert "Diebold-Mariano" in client.get("/method/").text


def test_not_found(client):
    response = client.get("/no-such-page/")
    assert response.status_code == 404
    assert "Page not found" in response.text


def test_results_explain_the_period(client, prices):
    text = results_page(client, "MSFT").text
    assert re.search(
        r"Over this period the price moved [+−]\d+%\. It was higher 5 days\s+later after \d+ of the 504", text
    )
    assert re.search(r"Hansen's SPA test\): p = (0\.\d{3}|&lt; 0\.001)\.", text)
    # The fake prices carry no exchange, so the chart admits its future dates skip weekends only.
    assert "holiday calendar isn't known" in text


def test_new_runs_are_rate_limited(client, prices, settings):
    settings.BACKCAST_RUNS_PER_HOUR = 2
    assert client.get("/backtest/AAA/").status_code == 302
    assert client.get("/backtest/BBB/").status_code == 302
    limited = client.get("/backtest/CCC/")
    assert limited.status_code == 429
    assert "the limit" in limited.text
    assert client.get("/backtest/AAA/").status_code == 302  # saved runs are free


def test_forwarded_address_is_only_trusted_when_configured(client, prices, settings):
    settings.BACKCAST_RUNS_PER_HOUR = 1
    # Untrusted: a client can't dodge the limit by inventing a header.
    assert client.get("/backtest/AAA/", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 302
    assert client.get("/backtest/BBB/", headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 429
    # Trusted: the proxy's last entry identifies the client.
    settings.BACKCAST_CLIENT_IP_HEADER = "X-Forwarded-For"
    assert client.get("/backtest/CCC/", headers={"X-Forwarded-For": "9.9.9.9, 3.3.3.3"}).status_code == 302
    assert client.get("/backtest/DDD/", headers={"X-Forwarded-For": "9.9.9.9, 4.4.4.4"}).status_code == 302


def test_each_price_source_keeps_its_own_runs(client, prices, monkeypatch):
    client.get("/backtest/MSFT/")
    monkeypatch.setenv("BACKCAST_PRICE_SOURCE", "csv")
    client.get("/backtest/MSFT/")
    assert prices == ["MSFT", "MSFT"]
    assert Run.objects.count() == 2
    assert "Daily closes from local CSV files." in client.get("/").text
