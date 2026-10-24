# The command line

Backcast installs one command, `backcast`, with two subcommands: `run` for a backtest and `web` for the web app.
`backcast --help` and `backcast run --help` print the options.

## backcast run

```
backcast run SYMBOL [--horizon {1,5,20}]
```

| Argument    | Meaning                                                                                         |
| ----------- | ----------------------------------------------------------------------------------------------- |
| `SYMBOL`    | The ticker, in the form your price source uses. Case doesn't matter: `aapl` and `AAPL` are equal. |
| `--horizon` | How many trading days ahead each forecast looks: 1, 5 or 20. The default is 5.                   |

Symbols can contain letters, digits and the characters `.`, `-`, `=` and `^`, which covers Yahoo's forms for other
exchanges (`SHOP.TO`), share classes (`BRK-B`), indices (`^GSPC`) and currencies (`EURUSD=X`).

Prices come from the configured [price source](price-sources.md). The environment variables in
[Configuration](configuration.md) apply to the command line as well as the web app, so this runs the same backtest
on your own file, `prices/AAPL.csv`:

```bash
BACKCAST_PRICE_SOURCE=csv BACKCAST_CSV_DIR=prices backcast run AAPL
```

### The output

This is a real run, with prices to 23 October 2026:

```
AAPL: 5-day forecasts from 504 trading days

Drift edged out the naive forecast by 0.6%, but the margin is within the noise. It predicted a rise on all 504 days while the price rose 50%, so the win says more about this period's trend than about forecasting skill.
SPA p-value that no model beats naive: 0.446

   model  mae_pct  relative_mae  relative_crps  relative_interval  win_rate  tie_rate  p_value  direction  coverage  p_adjusted
   Drift    3.085         0.994          0.995              1.002     0.544     0.000    0.423      0.562     0.827       1.000
   Theta    3.089         0.995          1.012              0.998     0.558     0.000    0.222      0.563     0.815       1.000
   ARIMA    3.096         0.997          0.999              1.003     0.157     0.722    0.283      0.535     0.827       1.000
     ETS    3.105         1.000          1.000              1.000     0.476     0.002    0.899      0.478     0.821       1.000
   Naive    3.105         1.000          1.000              1.000         —         —        —          —     0.821           —
LightGBM    3.106         1.000          1.011              1.034     0.490     0.000    0.985      0.522     0.677       1.000
```

The first line says how many forecasts were scored. The verdict below it names the most accurate model and whether
its edge over naive passed the significance test. When that model won by predicting the same direction almost every
day of a period that moved that way, the verdict adds a note saying so, as it does here. The SPA p-value is the
chance of a result at least this good if no model were really better than naive; the verdict counts a win as real
below 0.05.

The table has one row per model, most accurate first:

| Column              | Meaning                                                                                                   |
| ------------------- | --------------------------------------------------------------------------------------------------------- |
| `mae_pct`           | Mean absolute error of the log price, which reads as a percentage: 3.085 means the forecast missed by about 3.1% on average. |
| `relative_mae`      | `mae_pct` divided by naive's. Below 1 is more accurate than naive. The table is sorted by it.              |
| `relative_crps`     | The CRPS, which scores the whole forecast distribution rather than one number, divided by naive's.       |
| `relative_interval` | The 80% interval score divided by naive's. It charges for wide intervals and heavily for misses outside them. |
| `win_rate`          | Share of the forecasts in which the model was closer to the actual price than naive.                     |
| `tie_rate`          | Share in which it forecast exactly what naive did. Ties count as neither a win nor a loss.               |
| `p_value`           | Diebold-Mariano test of the model against naive, on its own.                                             |
| `direction`         | Share of forecasts that called the direction of the move correctly. Forecasts of no change are left out. |
| `coverage`          | Share of actual prices that landed inside the model's 80% interval. Close to 0.80 is well calibrated.    |
| `p_adjusted`        | `p_value` after Holm's adjustment for testing five models at once. This is the one to read.              |

Naive has no `win_rate`, `tie_rate`, `p_value` or `direction`, because it would be compared with itself.
[Reading the results](reading-results.md) explains each metric and test in depth.

### Exit status

| Code | When                                                                                                   |
| ---- | ------------------------------------------------------------------------------------------------------ |
| 0    | The backtest ran.                                                                                      |
| 1    | It couldn't: an unknown symbol, too little history, an unreachable or misconfigured price source. The reason is printed to standard error, starting with `backcast:`. |
| 2    | The arguments were wrong, for example an unsupported horizon.                                          |

Because errors go to standard error and the exit code says whether it worked, the command is safe to use in shell
scripts:

```bash
for symbol in AAPL MSFT NVDA; do
  backcast run "$symbol" --horizon 20 > "results-$symbol.txt" || echo "$symbol failed"
done
```

## backcast web

```
backcast web [ADDRESS]
```

Starts the web app on Django's development server. `ADDRESS` is `host:port` and defaults to `127.0.0.1:8000`. Set
`BACKCAST_DEBUG=1` to see full error pages while you work on the code. For anything public, use a production server
as described in [Deployment](deployment.md).
