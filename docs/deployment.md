# Deployment

The web app is a small Django project with no database and no user accounts. In production, run it with Gunicorn
behind a reverse proxy such as nginx or Caddy that handles HTTPS.

## Running it

```bash
export BACKCAST_SECRET_KEY='a long random value'
export BACKCAST_ALLOWED_HOSTS=backcast.example.com
uv run gunicorn --workers 2 --timeout 120 --bind 127.0.0.1:8000 backcast.web.wsgi
```

Check the settings before you go live. This reports anything Django considers unsafe for production:

```bash
uv run django-admin check --deploy --settings=backcast.web.settings
```

With a secret key set, it gives three warnings, all expected:

- `security.W003`, no CSRF middleware: Backcast has no forms that change anything, and every request is a GET.
- `security.W004` and `security.W008`, no HSTS and no HTTPS redirect: your reverse proxy should handle both, since
  it is the part that terminates HTTPS.

Every setting is listed in [Configuration](configuration.md). The ones that matter in production:

- `BACKCAST_SECRET_KEY`: always set it. The built-in fallback is public.
- `BACKCAST_ALLOWED_HOSTS`: the domain names the site is served on. Requests for any other host get an error.
- `BACKCAST_CLIENT_IP_HEADER`: see the next section.
- `BACKCAST_CACHE_DIR`: a folder that every worker can write to and that survives restarts.

## Workers and timeouts

A backtest runs inside the request that asks for it, using a pool of worker processes, one per CPU core. Each
Gunicorn worker starts its own pool the first time it runs a backtest. On a small machine, one or two Gunicorn
workers are plenty; more just compete for the same cores.

A backtest takes 10 to 20 seconds on four cores and longer on smaller servers, which is why the example raises
Gunicorn's default 30-second timeout to 120. If backtests time out on your server, raise it further.

## Caching

Prices are cached for an hour, backtest results for six hours, and search suggestions for an hour. The cache is a
folder on disk, set by `BACKCAST_CACHE_DIR`, so every Gunicorn worker shares it. Deleting the folder clears the cache
and is always safe.

## Rate limiting and proxies

An uncached backtest keeps every core busy for several seconds, so each visitor can start
`BACKCAST_RUNS_PER_HOUR` new ones per hour, 30 by default. Results already in the cache don't count.

Visitors are told apart by their IP address. Behind a reverse proxy, every request comes from the proxy's address,
so all visitors would share one limit. Set `BACKCAST_CLIENT_IP_HEADER` to the header your proxy adds with the
visitor's address, usually `X-Forwarded-For`. Backcast reads the last address in that header, the one your proxy
added. Only set it when a proxy you control sits in front of the app and sets that header on every request: if the
app is reachable directly, anyone can send the header and choose their own address.

## Static files

[WhiteNoise](https://whitenoise.readthedocs.io) serves the CSS, JavaScript and fonts straight from the installed
package, so the proxy doesn't need to serve them. Running `collectstatic` is optional;
if you do, set `BACKCAST_STATIC_ROOT` to the folder it should fill.

## Prices on a public site

The default price source is Yahoo Finance through yfinance, which is meant for personal use, and Yahoo often
throttles requests from cloud servers. For a public deployment, consider a provider whose licence covers it, plugged
in as described in [Price sources](price-sources.md#writing-your-own-source). If you keep Yahoo, say so on the site,
as the footer does by default.

## Security

Backcast sends a strict Content Security Policy that only allows its own scripts, styles, fonts and images. It sets no
cookies and stores nothing about visitors beyond the per-hour run counts, which are keyed by IP address and expire
after an hour. Please report security problems privately, as described in
[SECURITY.md](../SECURITY.md).
