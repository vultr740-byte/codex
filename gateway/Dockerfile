FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV CODEX_UNSAFE_ALLOW_NO_SANDBOX=1
ENV PYTHONPATH=/app/gateway/src:/app/sdk/python/src
ENV DATA_DIR=/data
ENV CODEX_WORKING_DIR=/data/workspace

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
  ca-certificates \
  curl \
  git \
  nodejs \
  npm \
  ripgrep \
  && rm -rf /var/lib/apt/lists/*

ARG CODEX_NPM_SPEC=@openai/codex@latest
RUN npm install -g "${CODEX_NPM_SPEC}"

COPY sdk/python /app/sdk/python
RUN python -m pip install -e /app/sdk/python

COPY gateway /app/gateway
RUN python -m pip install -e /app/gateway

RUN mkdir -p /data /data/workspace

CMD ["python", "-m", "codex_gateway"]
