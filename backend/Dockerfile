FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.3 /uv /usr/local/bin/uv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --locked --no-dev
COPY backend/app ./app
COPY backend/migrations ./migrations
RUN useradd --system --uid 10001 ghostwriter
USER ghostwriter
ENV GW_HOST=0.0.0.0
EXPOSE 8000
CMD ["/app/.venv/bin/python", "-m", "app"]
