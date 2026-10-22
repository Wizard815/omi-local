# syntax=docker/dockerfile:1.7
# Omi local backend stack (backend + Firebase emulators + Redis) in one container.
# Mirrors the repo's local dev harness (scripts/dev-harness) with offline providers.
# Base: node for firebase-tools emulators; python 3.11 via uv (backend pins 3.11).
FROM node:22-bookworm-slim

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates git ffmpeg libopus-dev build-essential procps redis-server \
 && rm -rf /var/lib/apt/lists/*

# uv + pinned python 3.11.15 (backend/.python-version)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"
RUN uv python install 3.11.15

# Temurin JRE 21 for the Firebase emulators
RUN curl -fsSL "https://api.adoptium.net/v3/binary/latest/21/ga/linux/x64/jre/hotspot/normal/eclipse" -o /tmp/jre21.tar.gz \
 && mkdir -p /opt/jre21 && tar -xzf /tmp/jre21.tar.gz -C /opt/jre21 --strip-components=1 \
 && rm /tmp/jre21.tar.gz
ENV JAVA_HOME=/opt/jre21
ENV PATH="/opt/jre21/bin:$PATH"

# firebase-tools (emulators: firestore + auth)
RUN npm install -g firebase-tools@15.28.2

WORKDIR /app
# Dependencies first, from the lock alone, so a code edit does not reinstall
# ~300 packages: this layer only changes when the lock does. uv's download
# cache is a build mount, kept between builds and out of the image.
COPY backend/pylock.toml backend/.python-version ./backend/
RUN --mount=type=cache,target=/root/.cache/uv \
    cd backend && uv venv --python 3.11.15 .venv \
 && uv pip sync pylock.toml --python .venv/bin/python --compile-bytecode
COPY .github/scripts/desktop_release_manifest.py ./backend/desktop_release_manifest_contract.py
# What lands here is bounded by Dockerfile.dockerignore (no tests, scratch
# audio, stray venvs or the rest of the monorepo).
COPY backend/ ./backend/
COPY firebase.json firestore.rules firestore.indexes.json ./

COPY omi-host/entrypoint.sh /entrypoint.sh
COPY omi-host/local_llm_adapter.py /app/omi-host/local_llm_adapter.py
COPY omi-host/audio-env.sh /app/omi-host/audio-env.sh
COPY omi-host/auth-env.sh /app/omi-host/auth-env.sh
COPY omi-host/maintenance.sh /app/omi-host/maintenance.sh
RUN chmod +x /entrypoint.sh

ENV PATH="/app/backend/.venv/bin:$PATH"
EXPOSE 8000 8085
ENTRYPOINT ["/entrypoint.sh"]
