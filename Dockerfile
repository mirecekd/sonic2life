FROM python:3.12-slim

# System deps for audio processing
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        pipx \
    && rm -rf /var/lib/apt/lists/*

# Ensure pipx/uvx is on PATH
ENV PATH="/root/.local/bin:$PATH"
RUN pipx ensurepath

WORKDIR /app

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY app/ ./app/
COPY .env.example .env

# Expose port
EXPOSE 5005

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5005/health')"

# Run (--proxy-headers + --forwarded-allow-ips for nginx reverse proxy / wss:// support)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "5005", "--proxy-headers", "--forwarded-allow-ips", "*"]
