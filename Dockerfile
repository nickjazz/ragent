FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Dependency layer: install third-party packages without the project first, so
# source edits do not invalidate the heavy dependency cache.
COPY pyproject.toml uv.lock ./
COPY packages/ packages/
RUN uv sync --frozen --no-dev --no-install-project

COPY src/ src/
COPY migrations/ migrations/
COPY alembic/ alembic/
COPY alembic.ini ./
COPY resources/ resources/
RUN uv sync --frozen --no-dev --no-editable

ENV PYTHONUNBUFFERED=1

# Default: API process (uvicorn factory, binds 0.0.0.0:8000).
# Override CMD for other processes:
#   worker:     /app/.venv/bin/python -m ragent.worker
#   reconciler: /app/.venv/bin/python -m ragent.reconciler
CMD ["/app/.venv/bin/uvicorn", "ragent.bootstrap.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
