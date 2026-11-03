"""Browser tests for what the view tests can't reach: the JavaScript. They drive the installed Google Chrome against
a live server with fake prices and cheap models, so like the other tests they never touch the network.
"""

import os
import re
from datetime import date

import numpy as np
import pandas as pd
import pytest
from django.core.cache import cache
from playwright.sync_api import expect

from backcast.evaluate import run_backtest
from backcast.models import MODELS_BY_KEY, Naive
from backcast.web import tasks, views
from backcast.web.models import Run

# Playwright's sync API runs an event loop in the test thread, and Django's ORM won't run in one unless told it's safe.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

MODELS = [Naive(), MODELS_BY_KEY["drift"], MODELS_BY_KEY["lightgbm"]]
PRICES = pd.Series(
    100 * np.exp(np.cumsum(np.random.default_rng(0).normal(0, 0.01, 1250))),
    index=pd.bdate_range(end="2026-10-23", periods=1250),
)
CUMULATIVE = '[data-chart="cumulative"] .chart-plot svg'


@pytest.fixture(autouse=True)
def setup(settings, monkeypatch, page):
    """Fake prices and models, and fail the test on any error in the browser console."""
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    cache.clear()
    monkeypatch.setattr(tasks, "load_prices", lambda symbol: PRICES.rename(symbol))
    monkeypatch.setattr(tasks, "run_backtest", lambda prices, horizon: run_backtest(prices, horizon, MODELS))
    errors = []
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    page.on("pageerror", lambda error: errors.append(str(error)))
    yield
    assert errors == []


def test_backtest_charts_follow_the_model_picker(page, live_server):
    page.goto(f"{live_server.url}/backtest/TEST/?h=5")
    expect(page).to_have_url(re.compile(r"/runs/\w+/$"))
    expect(page.locator('[data-chart="price"] .chart-plot svg')).to_be_visible()
    cumulative = page.locator(CUMULATIVE)
    expect(cumulative).to_be_visible()

    picked = page.locator(".model-picker input:not(:checked) + label").first
    name = picked.inner_text()
    picked.click()
    expect(cumulative).to_have_attribute("aria-label", re.compile(re.escape(name)))
    page.locator('[data-chart="cumulative"] summary').click()
    expect(page.locator('[data-chart="cumulative"] .chart-data tbody tr').first).to_be_visible()


def test_the_theme_toggle_redraws_the_charts(page, live_server):
    page.emulate_media(color_scheme="light")
    page.goto(f"{live_server.url}/backtest/TEST/?h=5")
    expect(page.locator(CUMULATIVE)).to_be_visible()
    page.evaluate("selector => { window.before = document.querySelector(selector).outerHTML }", CUMULATIVE)

    page.locator("[data-theme-toggle]").click()
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    expect(page.locator("[data-theme-toggle]")).to_have_attribute("aria-pressed", "true")
    page.wait_for_function("selector => document.querySelector(selector).outerHTML !== window.before", arg=CUMULATIVE)


def test_a_run_page_shows_the_results_when_the_worker_finishes(page, live_server):
    run = Run.objects.create(
        id="0123456789abcdef",
        symbol="TEST",
        horizon=5,
        source="yahoo",
        data_date=date(2026, 10, 23),
        code_version="test",
        settings=tasks.run_settings(5),
    )
    page.goto(f"{live_server.url}/runs/{run.id}/")
    expect(page.locator(".progress")).to_have_text("Waiting for the worker to start this run.")

    run.status, run.html = Run.DONE, '<section class="verdict"><h2 class="headline">Finished.</h2></section>'
    run.save()
    expect(page.locator(".headline")).to_have_text("Finished.")
    expect(page.locator("#results")).not_to_have_attribute("aria-busy", "true")


def test_score_an_uploaded_csv(page, live_server):
    dates = PRICES.index[-305:-1]
    forecasts = pd.DataFrame({"date": dates.strftime("%Y-%m-%d"), "forecast": PRICES.loc[dates] * 1.002})
    page.goto(f"{live_server.url}/score/")
    page.locator("#symbol").fill("TEST")
    page.locator("#forecasts").set_input_files(
        {"name": "forecasts.csv", "mimeType": "text/csv", "buffer": forecasts.to_csv(index=False).encode()}
    )
    page.get_by_role("button", name="Score forecasts").click()

    expect(page.locator(".headline")).to_contain_text("Your forecasts")
    expect(page.locator(CUMULATIVE)).to_be_visible()
    expect(page.locator('[data-chart="price"]')).to_have_count(0)


def test_symbol_search(page, live_server, monkeypatch):
    found = [{"symbol": "TEST", "name": "Test Corporation", "exchange": "NMS"}]
    monkeypatch.setattr(views, "search_symbols", lambda query, limit=8: found)

    page.goto(f"{live_server.url}/score/")
    page.locator("#symbol").fill("test")
    page.get_by_role("option", name=re.compile("Test Corporation")).click()
    expect(page.locator("#symbol")).to_have_value("TEST")
    # Picking only fills the field here. Submitting would fail the form's check for a file and move focus to it.
    expect(page.locator("#symbol")).to_be_focused()

    page.goto(live_server.url)
    page.locator("#symbol").fill("test")
    page.locator("#symbol").press("ArrowDown")
    page.locator("#symbol").press("Enter")
    expect(page).to_have_url(re.compile(r"/runs/\w+/$"))


@pytest.mark.parametrize("path", ["/", "/backtest/TEST/?h=5", "/score/", "/findings/", "/models/", "/method/"])
def test_pages_fit_a_phone(page, live_server, path):
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{live_server.url}{path}")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    if path == "/findings/":
        expect(page.locator('[data-chart="p-values"] svg').first).to_be_visible()
