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

COPY codex-weixin-bridge/package.json codex-weixin-bridge/package-lock.json /app/codex-weixin-bridge/
WORKDIR /app/codex-weixin-bridge
RUN npm ci
COPY codex-weixin-bridge/ /app/codex-weixin-bridge/
RUN npm run build \
    && npm prune --omit=dev \
    && npm cache clean --force

WORKDIR /app

COPY scripts/codex-app-server-common.sh /usr/local/lib/codex-app-server-common.sh
COPY scripts/app-server-run.sh /usr/local/bin/app-server-run
COPY scripts/app-server-start.sh /usr/local/bin/app-server-start
COPY scripts/app-server-with-weixin-start.sh /usr/local/bin/app-server-with-weixin-start
RUN npm install -g "@openai/codex@${CODEX_NPM_VERSION}" \
    && chmod +x \
        /usr/local/bin/app-server-run \
        /usr/local/bin/app-server-start \
        /usr/local/bin/app-server-with-weixin-start

ENV CODEX_HOME=/data/.codex
ENV LOG_FORMAT=json
ENV RUST_LOG=info
ENV OPENAI_BASE_URL=https://clawfather.up.railway.app/v1

CMD ["/usr/local/bin/app-server-start"]
