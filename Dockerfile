FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:${PATH}"

WORKDIR /app

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY configs ./configs
COPY scripts ./scripts
COPY src ./src

RUN uv sync --frozen --no-dev

CMD ["uv", "run", "--no-dev", "python", "scripts/run_pipeline.py", "2024-04-30", "--lookback-days", "1"]
