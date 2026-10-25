# agent-hermes -- the runtime of a Hermes-based Datacore agent (Tris first).
#
# Code lives in the image, state lives in the agent's volume mounted at the
# agent's HOME. The volume's ~/.hermes/hermes-agent and ~/.hermes/node become
# symlinks into /opt (agent-prepare), so every path the agent's config and
# scripts already use keeps working unchanged.
#
# Versions reproduce what the first agent (Tris) ran on his own host on 2026-10-25:
# hermes-agent 0.19.0 with the exact 204-package set in requirements.lock
# (Python 3.11), Node 22.22.2 with the Datacore and PLUR CLIs, and the system
# Python packages his scheduled jobs import. The qualified, patched runtime
# (lib/hermes_runtime, 0.19.0+datacore.1 on Python 3.12) is the next upgrade,
# deliberately not part of the move.
FROM ubuntu:24.04@sha256:68ee22004a9109b44973313b02ff7b1de250f1de0f5ecb628d73f579aaff33ac

ARG NODE_VERSION=22.22.2
ARG NODE_SHA256=88fd1ce767091fd8d4a99fdb2356e98c819f93f3b1f8663853a2dee9b438068a
ARG UV_VERSION=0.11.7
ARG SUPERCRONIC_VERSION=v0.2.33
ARG SUPERCRONIC_SHA256=feefa310da569c81b99e1027b86b27b51e6ee9ab647747b49099645120cfc671
ARG PYTHON_VERSION=3.11.15

ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    PATH=/opt/hermes-agent/venv/bin:/opt/node/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# OS tools the agent and its jobs call, plus the shared libraries the local
# browser engines (Playwright Chromium, Camoufox) need at run time.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl git openssh-client netcat-openbsd tini jq sqlite3 \
      procps rsync less xz-utils ffmpeg gh \
      python3 python3-pip python3-venv python3-yaml \
      libnss3 libnspr4 libatk1.0-0t64 libatk-bridge2.0-0t64 libcups2t64 \
      libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
      libgbm1 libpango-1.0-0 libcairo2 libasound2t64 libatspi2.0-0t64 \
      libgtk-3-0t64 libx11-xcb1 libdbus-glib-1-2 libxtst6 fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Ubuntu images ship user "ubuntu" as uid 1000; agents run as uid 1000 (the
# owner of their files on the old host). HOME comes from the chart: it is the
# agent's old home path, where its volume is mounted.
RUN userdel -r ubuntu && useradd -m -d /var/lib/agent -u 1000 -s /bin/bash agent

RUN curl -fsSLo /tmp/node.tar.xz https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz \
    && echo "${NODE_SHA256}  /tmp/node.tar.xz" | sha256sum -c - \
    && mkdir -p /opt/node && tar -xJf /tmp/node.tar.xz -C /opt/node --strip-components=1 \
    && rm /tmp/node.tar.xz \
    && /opt/node/bin/npm install -g --prefix /opt/node \
         @datacore-one/cli@2.6.1 @datacore-one/mcp@2.3.0 @plur-ai/cli@0.22.1 @plur-ai/mcp@0.22.1 \
    && /opt/node/bin/npm cache clean --force

RUN curl -fsSLo /usr/local/bin/supercronic \
      https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-amd64 \
    && echo "${SUPERCRONIC_SHA256}  /usr/local/bin/supercronic" | sha256sum -c - \
    && chmod 755 /usr/local/bin/supercronic

# System Python packages the scheduled jobs import (org-workspace and friends).
RUN pip3 install --no-cache-dir --break-system-packages \
      org-workspace==0.7.1 ruamel.yaml==0.19.1 feedparser==6.0.14 gTTS==2.5.4 \
      defusedxml==0.7.1 youtube-transcript-api==1.2.4 yt-dlp==2026.10.21 \
      cryptography==50.0.2 markdown-it-py==4.2.0

# Hermes in its own Python 3.11, from the pinned package set.
COPY requirements.lock /opt/hermes-agent/requirements.lock
RUN pip3 install --no-cache-dir --break-system-packages uv==${UV_VERSION} \
    && UV_PYTHON_INSTALL_DIR=/opt/python uv python install ${PYTHON_VERSION} \
    && UV_PYTHON_INSTALL_DIR=/opt/python uv venv --python ${PYTHON_VERSION} /opt/hermes-agent/venv \
    && uv pip install --no-cache --python /opt/hermes-agent/venv/bin/python \
         --no-deps -r /opt/hermes-agent/requirements.lock \
    && /opt/hermes-agent/venv/bin/python -c "import hermes_cli, plur_hermes, telegram" \
    && chown -R 1000:1000 /opt/hermes-agent

COPY agent-prepare agent-run /usr/local/bin/
RUN chmod 755 /usr/local/bin/agent-prepare /usr/local/bin/agent-run

# Run-time PATH: system Python first (the jobs import its packages), Hermes last.
ENV PATH=/var/lib/agent/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/opt/node/bin:/opt/hermes-agent/venv/bin
USER 1000
WORKDIR /var/lib/agent
ENTRYPOINT ["/usr/bin/tini", "--"]
