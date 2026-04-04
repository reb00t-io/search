FROM python:3.13-slim

WORKDIR /app
COPY pyproject.toml VERSION TODO.md ./
COPY config/ ./config/
COPY docs/ ./docs/
COPY scripts/ ./scripts/
COPY src/ ./src/
COPY serving/ ./serving/
COPY ingestion/ ./ingestion/
COPY filtering/ ./filtering/
COPY indexing/ ./indexing/
RUN pip install --no-cache-dir .
ARG DEPLOY_DATE=unknown
ENV DEPLOY_DATE=$DEPLOY_DATE

RUN useradd --create-home appuser && mkdir -p /data && chown appuser:appuser /data
USER appuser

# Default: web server. Override via docker-compose command.
CMD ["python", "src/main.py"]
