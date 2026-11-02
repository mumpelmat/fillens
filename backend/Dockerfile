FROM ghcr.io/astral-sh/uv:0.12.3 AS uv
FROM python:3.12-slim
COPY --from=uv /uv /usr/local/bin/uv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
RUN groupadd -g 10001 fillens && useradd -u 10001 -g fillens -M fillens && mkdir -p /data /ml && chown fillens:fillens /data /ml
COPY app ./app
COPY alembic.ini ./
COPY migrations ./migrations
USER fillens
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log"]
