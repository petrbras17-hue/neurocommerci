FROM node:24-slim AS frontend-build

WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --legacy-peer-deps
COPY frontend/ .
RUN npm run build


FROM python:3.11-slim

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    redis \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY . .
COPY --from=frontend-build /frontend/dist /app/frontend/dist

# Create non-root user for runtime
RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid appuser --shell /bin/bash --create-home appuser

# Create data directories with correct ownership
RUN mkdir -p data/sessions data/logs data/avatars && \
    chown -R appuser:appuser /app

# Persistent runtime data mount point
VOLUME ["/app/data"]

USER appuser

CMD ["python", "main.py"]
