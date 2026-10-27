# agent-openclaw -- the runtime of an OpenClaw-based Datacore agent (Data).
#
# Built on agent-base. OpenClaw 2026.9.3, the version Data runs, needs Node 24
# (engines: >=24.16). Its config, sessions and plugin registry live in the
# volume ($OPENCLAW_STATE_DIR, set by the chart); the Datacore tool gate is a
# plugin linked from the agent's runner checkout, as on its old host, so the
# runner's pinned commit decides the gate's code.
ARG BASE=agent-base:3
FROM ${BASE}

ARG NODE_VERSION=24.21.0
ARG NODE_SHA256=fd8e59d5a511510f6a298afb548f18c7d2b1be404d8b4a27d94fbe49f56cb2d6
ARG OPENCLAW_VERSION=2026.9.3

ENV PATH=/opt/node/bin:$PATH
RUN curl -fsSLo /tmp/node.tar.xz https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz \
    && echo "${NODE_SHA256}  /tmp/node.tar.xz" | sha256sum -c - \
    && mkdir -p /opt/node && tar -xJf /tmp/node.tar.xz -C /opt/node --strip-components=1 \
    && rm /tmp/node.tar.xz \
    && /opt/node/bin/npm install -g --prefix /opt/node \
         openclaw@${OPENCLAW_VERSION} \
         @datacore-one/cli@2.6.1 @datacore-one/mcp@2.3.0 @plur-ai/cli@0.22.1 @plur-ai/mcp@0.22.1 \
    && /opt/node/bin/npm cache clean --force

RUN /opt/node/bin/openclaw --version | grep -q "OpenClaw ${OPENCLAW_VERSION}"

COPY prepare.d/ /usr/local/lib/agent-prepare.d/
RUN chmod 755 /usr/local/lib/agent-prepare.d/*

ENV PATH=/var/lib/agent/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/opt/node/bin
USER 1000
