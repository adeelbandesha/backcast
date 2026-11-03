"""Walk-forward benchmarks for stock forecasting models.

    from backcast import load_prices, run_backtest

    report = run_backtest(load_prices("AAPL"), horizon=5)

`help()` on any of the names in `__all__` explains it.
"""

from . import volatility
from .data import load_prices
from .evaluate import headline, run_backtest, score, walk_forward

__all__ = ["headline", "load_prices", "run_backtest", "score", "volatility", "walk_forward"]
