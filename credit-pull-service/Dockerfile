# Credit Pull Microservice — Railway-ready Dockerfile
# Python 3.9 + system deps for Playwright Chromium + gunicorn

FROM python:3.9-slim

# System packages that Playwright Chromium needs
RUN apt-get update && apt-get install -y --no-install-recommends \
        wget gnupg ca-certificates fonts-liberation \
        libasound2 libatk-bridge2.0-0 libatk1.0-0 libatspi2.0-0 \
        libc6 libcairo2 libcups2 libdbus-1-3 libdrm2 libexpat1 \
        libgbm1 libglib2.0-0 libnspr4 libnss3 libpango-1.0-0 \
        libpangocairo-1.0-0 libwayland-client0 libx11-6 libx11-xcb1 \
        libxcb1 libxcomposite1 libxdamage1 libxext6 libxfixes3 \
        libxkbcommon0 libxrandr2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps + Chromium binary in one step (cached unless requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install chromium

# Copy the app
COPY . .

ENV PORT=5000
EXPOSE 5000

# Gunicorn: 2 workers × 4 threads × 300s timeout (scrapes can take 60-90s)
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "4", \
     "--timeout", "300", "--access-logfile", "-", "app:app"]
