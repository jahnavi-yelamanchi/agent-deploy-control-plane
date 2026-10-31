# syntax=docker/dockerfile:1.7
# One image: runtime (default), rollback controller (python -m adcp.rollback.service),
# evaluator (python -m adcp.evals.online).
FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
# Dependency layer: cached until uv.lock / pyproject change.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project --extra providers
COPY src/ src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --extra providers

FROM python:3.13-slim
RUN groupadd --system --gid 10001 adcp \
 && useradd --system --uid 10001 --gid adcp --home-dir /app --shell /usr/sbin/nologin adcp
WORKDIR /app
COPY --from=build --chown=adcp:adcp /app/.venv /app/.venv
COPY --chown=adcp:adcp specs/ specs/
COPY --chown=adcp:adcp flags/ flags/
COPY --chown=adcp:adcp evals/tasks/ evals/tasks/
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MODEL_PROVIDER=mock \
    HOME=/tmp
USER 10001
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://localhost:8080/healthz', timeout=2)" || exit 1
CMD ["uvicorn", "adcp.runtime.app:app", "--host", "0.0.0.0", "--port", "8080"]
