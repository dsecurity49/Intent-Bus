# Use a slim Python image for a small footprint
FROM python:3.11-slim

# Environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=random \
    BUS_DB_PATH=/app/data/infrastructure.db

# Create non-root user
RUN useradd -r -s /usr/sbin/nologin intentbus

# Set working directory
WORKDIR /app

# Install required system packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    sqlite3 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Verify SQLite version supports RETURNING clauses
RUN sqlite3 --version | grep -qE "3\.(3[5-9]|[4-9][0-9])" || { echo "SQLite 3.35.0+ required"; exit 1; }

# Install Python dependencies
COPY server-requirements.txt .
RUN pip install --no-cache-dir -r server-requirements.txt

# Copy application files
COPY flask_app.py .

# Persistent SQLite storage directory
RUN mkdir -p /app/data && chown -R intentbus:intentbus /app

# Drop root privileges
USER intentbus

# Expose service port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://127.0.0.1:8080/health || exit 1

# SQLite is single-writer; use one Gunicorn worker process.
# Threads are acceptable because SQLite serializes writes internally.
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "8", "--timeout", "120", "--graceful-timeout", "30", "--access-logfile", "-", "--error-logfile", "-", "flask_app:app"]
