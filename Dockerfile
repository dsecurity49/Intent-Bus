# Use a slim Python image for a small footprint
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV BUS_DB_PATH=/app/data/infrastructure.db

# Create and set the working directory
WORKDIR /app

# Install system dependencies (SQLite 3.35+ required for RETURNING clause)
RUN apt-get update && apt-get install -y \
    sqlite3 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install production server and dependencies
RUN pip install --no-cache-dir Flask werkzeug gunicorn

# Copy the server script
COPY flask_app.py app.py

# Create directory for persistent database storage
RUN mkdir -p /app/data

# Expose the standard port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=5s \
    CMD curl -f http://localhost:8080/ || exit 1

# Single worker + threads keeps SQLite's single-writer model intact.
# Multiple workers = multiple processes = write contention on SQLite.
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "4", "app:app"]
