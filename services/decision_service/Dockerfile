# Build context is the repository root: docker build -f services/decision_service/Dockerfile .
ARG PYTHON_IMAGE=python:3.12-slim

FROM ${PYTHON_IMAGE} AS build
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
COPY services ./services
# Only the decision service and the pure-Python core of credit_score.
RUN uv sync --locked --no-default-groups --package decision-service --no-editable

FROM ${PYTHON_IMAGE}
RUN useradd --system --uid 10001 --no-create-home app
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY policy/manifest.json policy/credit-policy.json /app/policy/
ENV PATH="/app/.venv/bin:$PATH" POLICY_DIR=/app/policy PYTHONUNBUFFERED=1
USER app
EXPOSE 8001
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=2).status != 200)"]
CMD ["uvicorn", "decision_service.app:app", "--factory", "--host", "0.0.0.0", "--port", "8001"]
