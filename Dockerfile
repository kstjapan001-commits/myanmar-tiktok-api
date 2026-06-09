# rebuild v5.0.0 — youtube-transcript-api added
FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive

# System deps: ffmpeg + Myanmar font for subtitle rendering
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-noto \
    libass9 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -f

WORKDIR /app

# Install Python deps (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY main.py .

EXPOSE 8000

# Railway injects $PORT; Python code reads it
CMD ["python", "main.py"]
