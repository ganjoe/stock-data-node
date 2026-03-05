FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY src/ ./src/

# Create required directories (volumes will override at runtime)
RUN mkdir -p /app/config /app/default_config /app/data/parquet /app/watch /app/logs /app/state

# Copy default configurations to fallback directory
COPY config/ /app/default_config/

# Copy entrypoint script
COPY docker-entrypoint.sh /app/
RUN chmod +x /app/docker-entrypoint.sh

# Set environment variable so main.py can find the base dir
ENV APP_BASE_DIR=/app

# Expose REST API port
EXPOSE 8002

# Healthcheck via the /health endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8002/health')" || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python", "src/main.py"]
