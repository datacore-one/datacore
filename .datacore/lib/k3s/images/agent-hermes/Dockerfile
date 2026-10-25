# agent-hermes -- the runtime of a Hermes-based Datacore agent (Tris, Winston).
#
# Built on agent-base. Versions reproduce what Tris ran on his own host on
# 2026-10-25: hermes-agent 0.19.0 with the exact 204-package set in
# requirements.lock (Python 3.11), Node 22.22.2 with the Datacore and PLUR CLIs.
# The volume's ~/.hermes/hermes-agent and ~/.hermes/node become symlinks into
# /opt (prepare.d/50-hermes), so every path the agent's config and scripts
# already use keeps working unchanged.
ARG BASE=agent-base:2
FROM ${BASE}

ARG NODE_VERSION=22.22.2
ARG NODE_SHA256=88fd1ce767091fd8d4a99fdb2356e98c819f93f3b1f8663853a2dee9b438068a
ARG UV_VERSION=0.11.7
ARG PYTHON_VERSION=3.11.15

ENV PATH=/opt/node/bin:$PATH
RUN curl -fsSLo /tmp/node.tar.xz https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz \
    && echo "${NODE_SHA256}  /tmp/node.tar.xz" | sha256sum -c - \
    && mkdir -p /opt/node && tar -xJf /tmp/node.tar.xz -C /opt/node --strip-components=1 \
    && rm /tmp/node.tar.xz \
    && /opt/node/bin/npm install -g --prefix /opt/node \
         @datacore-one/cli@2.6.1 @datacore-one/mcp@2.3.0 @plur-ai/cli@0.22.1 @plur-ai/mcp@0.22.1 \
    && /opt/node/bin/npm cache clean --force

# Hermes in its own Python 3.11, from the pinned package set.
COPY requirements.lock /opt/hermes-agent/requirements.lock
RUN pip3 install --no-cache-dir --break-system-packages uv==${UV_VERSION} \
    && UV_PYTHON_INSTALL_DIR=/opt/python uv python install ${PYTHON_VERSION} \
    && UV_PYTHON_INSTALL_DIR=/opt/python uv venv --python ${PYTHON_VERSION} /opt/hermes-agent/venv \
    && uv pip install --no-cache --python /opt/hermes-agent/venv/bin/python \
         --no-deps -r /opt/hermes-agent/requirements.lock \
    && /opt/hermes-agent/venv/bin/python -c "import hermes_cli, plur_hermes, telegram" \
    && chown -R 1000:1000 /opt/hermes-agent

COPY prepare.d/ /usr/local/lib/agent-prepare.d/
RUN chmod 755 /usr/local/lib/agent-prepare.d/*

# Run-time PATH: system Python first (the jobs import its packages), Hermes last.
ENV PATH=/var/lib/agent/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/opt/node/bin:/opt/hermes-agent/venv/bin
USER 1000
