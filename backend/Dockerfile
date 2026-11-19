FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install system packages while still root. imageio-ffmpeg normally provides
# ffmpeg itself, but the OS package is a reliable production fallback.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && addgroup --system --gid 10001 app \
    && adduser --system --uid 10001 --ingroup app app

COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt \
    && python -m pip check

COPY court_transcription ./court_transcription
RUN mkdir -p /app/transcripts /app/uploads /var/tmp/court-transcription \
    && chown -R app:app /app /var/tmp/court-transcription

USER app
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "court_transcription.api_server:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
