# Build context is the repository root: docker build -f services/score_api/Dockerfile .
ARG PYTHON_IMAGE=python:3.12-slim

FROM ${PYTHON_IMAGE} AS build
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
COPY services ./services
# Only the API and the pure-Python core of credit_score: no training stack.
RUN uv sync --locked --no-default-groups --package score-api --no-editable

FROM ${PYTHON_IMAGE}
RUN useradd --system --uid 10001 --no-create-home app
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY models/ /app/models/
ENV PATH="/app/.venv/bin:$PATH" MODELS_DIR=/app/models PYTHONUNBUFFERED=1
USER app
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status != 200)"]
CMD ["uvicorn", "score_api.app:app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
