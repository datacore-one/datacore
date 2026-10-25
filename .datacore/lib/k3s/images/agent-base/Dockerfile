# agent-base -- what every Datacore agent image shares, whatever its runtime.
#
# OS tools, Ubuntu's Python libraries and the system Python packages the
# agent's scheduled jobs import, supercronic, the agent user (uid 1000), the
# per-role lock (agent-run) and the start guard (agent-prepare). Code lives in
# the images, state in the agent's volume mounted at the agent's HOME.
# A runtime image (agent-hermes, agent-openclaw) adds its own Node and runtime,
# and its own steps under /usr/local/lib/agent-prepare.d.
#
# Build context is .datacore/lib/k3s (it copies bin/fleetctl):
#   docker build -f images/agent-base/Dockerfile -t agent-base:N .
FROM ubuntu:24.04@sha256:68ee22004a9109b44973313b02ff7b1de250f1de0f5ecb628d73f579aaff33ac

ARG SUPERCRONIC_VERSION=v0.2.33
ARG SUPERCRONIC_SHA256=feefa310da569c81b99e1027b86b27b51e6ee9ab647747b49099645120cfc671
ARG KUBECTL_VERSION=v1.31.4
ARG KUBECTL_SHA256=298e19e9c6c17199011404278f0ff8168a7eca4217edad9097af577023a5620f

ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# OS tools the agent and its jobs call, plus the shared libraries the local
# browser engines (Playwright Chromium, Camoufox) need at run time.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl git openssh-client netcat-openbsd tini jq sqlite3 \
      procps rsync less xz-utils ffmpeg gh \
      python3 python3-pip python3-venv python3-yaml \
      # Ubuntu's general Python libraries, as on the old host: the duties run on
      # this Python (pytest needs pygments; promise evals failed without it).
      python3-pygments python3-packaging python3-jinja2 python3-markupsafe \
      python3-dateutil python3-tz python3-six python3-attr python3-jsonschema \
      python3-typing-extensions python3-rich python3-markdown-it python3-mdurl \
      python3-jwt python3-distro python3-babel python3-chardet python3-colorama \
      python3-requests python3-click python3-icalendar python3-vobject \
      python3-cryptography python3-openssl python3-socks python3-brotli \
      python3-zstandard python3-setuptools python3-pyparsing python3-pexpect \
      python3-uritemplate python3-httplib2 python3-oauthlib python3-boto3 \
      libnss3 libnspr4 libatk1.0-0t64 libatk-bridge2.0-0t64 libcups2t64 \
      libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
      libgbm1 libpango-1.0-0 libcairo2 libasound2t64 libatspi2.0-0t64 \
      libgtk-3-0t64 libx11-xcb1 libdbus-glib-1-2 libxtst6 fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Ubuntu images ship user "ubuntu" as uid 1000; agents run as uid 1000 (the
# owner of their files on the old host). HOME comes from the chart: it is the
# agent's old home path, where its volume is mounted.
RUN userdel -r ubuntu && useradd -m -d /var/lib/agent -u 1000 -s /bin/bash agent

RUN curl -fsSLo /usr/local/bin/supercronic \
      https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-amd64 \
    && echo "${SUPERCRONIC_SHA256}  /usr/local/bin/supercronic" | sha256sum -c - \
    && chmod 755 /usr/local/bin/supercronic

# System Python packages the scheduled jobs import (org-workspace and friends).
RUN pip3 install --no-cache-dir --break-system-packages \
      org-workspace==0.7.1 ruamel.yaml==0.19.1 feedparser==6.0.14 gTTS==2.5.4 \
      defusedxml==0.7.1 youtube-transcript-api==1.2.4 yt-dlp==2026.10.21 \
      cryptography==50.0.2 markdown-it-py==4.2.0

# The fleet kill switch, for an agent whose ServiceAccount may scale the fleet
# (chart: killSwitch). Without that account it can do nothing.
RUN curl -fsSLo /usr/local/bin/kubectl https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl \
    && echo "${KUBECTL_SHA256}  /usr/local/bin/kubectl" | sha256sum -c - \
    && chmod 755 /usr/local/bin/kubectl
COPY bin/fleetctl /usr/local/bin/fleetctl
COPY images/agent-base/agent-prepare images/agent-base/agent-run /usr/local/bin/
RUN chmod 755 /usr/local/bin/agent-prepare /usr/local/bin/agent-run /usr/local/bin/fleetctl \
    && mkdir -p /usr/local/lib/agent-prepare.d

WORKDIR /var/lib/agent
ENTRYPOINT ["/usr/bin/tini", "--"]
