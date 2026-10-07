FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --no-create-home --uid 10001 butler
WORKDIR /app
COPY butler/ /app/butler/
COPY assets/ /app/assets/
USER butler
ENV PYTHONUNBUFFERED=1
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request as u;u.urlopen('http://127.0.0.1:%s/healthz'%os.environ.get('STATUS_PORT','8080'),timeout=2)"
CMD ["python", "-m", "butler"]
