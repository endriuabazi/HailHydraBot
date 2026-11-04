# yt-dlp needs Node.js 22+ for YouTube's JS challenges; Debian's nodejs package is too old,
# so the binary is copied from the official Node image (same Debian release for glibc compatibility).
FROM node:22-bookworm-slim AS node

FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=node /usr/local/bin/node /usr/local/bin/node

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY bot.py .

RUN useradd --create-home --uid 10001 hydra
USER hydra

CMD ["python", "bot.py"]
