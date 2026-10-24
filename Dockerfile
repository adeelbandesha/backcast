FROM python:3.13-slim AS build

# statsforecast has no wheel for Python 3.13, so it compiles here; the final image doesn't carry the compiler.
RUN apt-get update && apt-get install -y --no-install-recommends g++ && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
# Dependencies first, so a code change doesn't reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY README.md LICENSE ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable


FROM python:3.13-slim

# LightGBM needs the OpenMP runtime.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
COPY --from=build /app/.venv /app/.venv

# Recorded with every run and part of its id. Without it, rebuilt code would reuse runs saved by the old code.
ARG BACKCAST_COMMIT
RUN test -n "$BACKCAST_COMMIT" || { echo "Build with BACKCAST_COMMIT=\$(git describe --always --dirty)"; exit 1; }
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=backcast.web.settings \
    BACKCAST_COMMIT=$BACKCAST_COMMIT \
    BACKCAST_DATA_DIR=/data \
    BACKCAST_CACHE_DIR=/data/cache

RUN useradd --create-home backcast && mkdir /data && chown backcast /data
USER backcast
VOLUME /data
EXPOSE 8000
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--access-logfile", "-", "backcast.web.wsgi"]
