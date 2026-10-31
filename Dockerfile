FROM python:3.12-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUTF8=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

COPY requirements.txt constraints.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home app \
    && mkdir /app/state \
    && chown app:app /app/state

COPY document_automation/ ./document_automation/
COPY tools/ ./tools/
COPY data/samples/ ./data/samples/
COPY data/ground_truth.json data/ground_truth.csv ./data/
USER 10001:10001

FROM base AS test
USER root
COPY requirements-dev.txt ./
RUN python -m pip install --no-cache-dir -r requirements-dev.txt
COPY --chown=10001:10001 tests/ ./tests/
USER 10001:10001
CMD ["python", "-m", "unittest", "discover", "-s", "tests", "-v"]

FROM base AS runtime
EXPOSE 8000
CMD ["python", "-m", "document_automation", "serve", "--demo", "--host", "0.0.0.0", "--database", "/app/state/demo.sqlite3", "--uploads", "/app/state/uploads", "--output-dir", "/app/state/exports"]
