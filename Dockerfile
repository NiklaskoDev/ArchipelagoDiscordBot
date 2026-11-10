# syntax=docker/dockerfile:1

# Archipelago Discord bot: talks to an Archipelago Manager over its REST API.
#
#   docker build -t ap-discord-bot .
ARG PYTHON_VERSION=3.12

# ---------------------------------------------------------------------------------------------------------------------
# Stage 1: the bot's Python environment, from the pinned requirements.
# ---------------------------------------------------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY requirements.txt /tmp/requirements.txt
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install -r /tmp/requirements.txt

# ---------------------------------------------------------------------------------------------------------------------
# Final image
# ---------------------------------------------------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm
ARG VERSION=dev

LABEL org.opencontainers.image.title="Archipelago Discord bot" \
      org.opencontainers.image.description="Discord bot for Archipelago multiworld games, using the Archipelago Manager's REST API" \
      org.opencontainers.image.source="https://github.com/NiklaskoDev/ArchipelagoDiscordBot" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}"

# tini passes SIGTERM on and reaps child processes. tzdata makes the TZ setting work (the slim image has none). The
# entrypoint drops root with util-linux's setpriv, which the base image already has.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tini tzdata \
 && rm -rf /var/lib/apt/lists/*

COPY --from=build /opt/venv /opt/venv
COPY apbot /app/apbot
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh \
 && /opt/venv/bin/python -m compileall -q /app/apbot

ENV DATA_DIR=/data \
    PUID=99 \
    PGID=100 \
    UMASK=022 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app
VOLUME /data

# Healthy while the bot is connected to Discord: it rewrites DATA_DIR/heartbeat every 30 seconds then.
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD python -c "import os, sys, time; sys.exit(0 if time.time() - os.path.getmtime(os.path.join(os.environ.get('DATA_DIR', '/data'), 'heartbeat')) < 90 else 1)"

STOPSIGNAL SIGTERM
ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh"]
CMD ["python", "-m", "apbot"]
