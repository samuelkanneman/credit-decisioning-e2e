# Build context is the repository root: docker build -f services/dashboard/Dockerfile .
ARG PYTHON_IMAGE=python:3.12-slim

FROM ${PYTHON_IMAGE} AS build
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
COPY services ./services
RUN uv sync --locked --no-default-groups --package dashboard --no-editable

FROM ${PYTHON_IMAGE}
RUN useradd --system --uid 10001 --create-home app
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY services/dashboard/src/dashboard/app.py /app/app.py
COPY models/scorecard-v1/card.json models/scorecard-v1/reference.json /app/model/
ENV PATH="/app/.venv/bin:$PATH" MODEL_DIR=/app/model PYTHONUNBUFFERED=1
USER app
EXPOSE 8501
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=2).status != 200)"]
CMD ["streamlit", "run", "app.py", "--server.headless=true", "--server.port=8501", \
     "--server.address=0.0.0.0", "--browser.gatherUsageStats=false"]
