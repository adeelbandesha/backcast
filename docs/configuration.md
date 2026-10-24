# Configuration

Backcast reads all its settings from environment variables. None are required to run it on your own machine.

## Prices

These apply to the command line, the web app and the Python API.

| Variable                | Default   | What it does                                                                         |
| ----------------------- | --------- | ------------------------------------------------------------------------------------ |
| `BACKCAST_PRICE_SOURCE` | `yahoo`   | Where prices come from: `yahoo`, `csv`, or the import path of your own source module. |
| `BACKCAST_CSV_DIR`      | unset     | The folder of price files. Required when the source is `csv`.                         |
| `BACKCAST_CSV_CALENDAR` | unset     | The trading calendar for CSV prices, such as `XNYS`. Unset means weekdays only.       |

[Price sources](price-sources.md) explains each one.

## Web app

| Variable                    | Default                                | What it does                                                    |
| --------------------------- | -------------------------------------- | --------------------------------------------------------------- |
| `BACKCAST_SECRET_KEY`       | an insecure key for local use          | Django's secret key. Set a long random value in production.     |
| `BACKCAST_ALLOWED_HOSTS`    | `localhost,127.0.0.1`                  | Comma-separated host names the app answers to.                  |
| `BACKCAST_DEBUG`            | off                                    | `1` shows Django's detailed error pages. Never in production.   |
| `BACKCAST_CACHE_DIR`        | `backcast-cache` in the temp folder    | Where prices, results and search suggestions are cached.        |
| `BACKCAST_RUNS_PER_HOUR`    | `30`                                   | New backtests one visitor may start per hour.                   |
| `BACKCAST_CLIENT_IP_HEADER` | unset                                  | The header your reverse proxy puts the visitor's address in.    |
| `BACKCAST_STATIC_ROOT`      | unset                                  | Where `collectstatic` copies static files, if you run it.       |
| `BACKCAST_LOG_LEVEL`        | `INFO`                                 | Logging level: `DEBUG`, `INFO`, `WARNING` or `ERROR`.           |

[Deployment](deployment.md) explains when each matters.

To generate a secret key:

```bash
python -c "import secrets; print(secrets.token_urlsafe(50))"
```

## Setting them

For a single command:

```bash
BACKCAST_PRICE_SOURCE=csv BACKCAST_CSV_DIR=~/prices backcast run AAPL
```

For a whole shell session:

```bash
export BACKCAST_PRICE_SOURCE=csv
export BACKCAST_CSV_DIR=~/prices
```

On a server, set them where your process manager or container platform keeps environment variables, such as a
systemd unit's `Environment=` lines or a container's environment settings. Restart the web app after changing them;
it reads them when it starts.
