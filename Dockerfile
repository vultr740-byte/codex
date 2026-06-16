FROM node:22-bookworm-slim

ARG CODEX_NPM_VERSION=0.140.0

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
        jq \
        ripgrep \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY scripts/app-server-start.sh /usr/local/bin/app-server-start
RUN npm install -g "@openai/codex@${CODEX_NPM_VERSION}" \
    && chmod +x /usr/local/bin/app-server-start

ENV CODEX_HOME=/data/.codex
ENV LOG_FORMAT=json
ENV RUST_LOG=info
ENV OPENAI_BASE_URL=https://clawfather.up.railway.app/v1

CMD ["/usr/local/bin/app-server-start"]
