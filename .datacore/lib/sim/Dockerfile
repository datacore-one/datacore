# The fleet week simulator's Linux box (fleet_week_sim.py, issue #222).
#
# Why Linux: libfaketime moves the clock of EVERY process it is preloaded
# into -- python, bash, git, date -- with no change to the code under test.
# macOS will not preload a library into its protected binaries, so the
# simulation runs here. The container is started with --network none: nothing
# in it can reach the real fleet, GitHub or Telegram, whatever a job tries.
#
#   docker build -t datacore-fleet-sim .datacore/lib/sim
FROM python:3.11-slim-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      git faketime rsync perl jq sqlite3 procps util-linux curl ca-certificates \
      bash coreutils findutils grep sed gawk tar gzip xz-utils \
 && rm -rf /var/lib/apt/lists/*

# The install's own requirements (.datacore/lib/requirements.txt, core part)
# plus what the promise evals import.
RUN pip install --no-cache-dir \
      "pyyaml>=6.0" "org-workspace==0.7.0" "cryptography>=50.0.0" "packaging>=24.0" \
      "markdown-it-py==4.2.0" requests \
      pytest hypothesis pytest-mock pytest-asyncio

# Jobs name their interpreter by the path it has on each host.
RUN ln -sf /usr/local/bin/python3 /usr/bin/python3 \
 && mkdir -p /opt/homebrew/bin && ln -sf /usr/local/bin/python3 /opt/homebrew/bin/python3

ENV TZ=UTC LANG=C.UTF-8 PYTHONDONTWRITEBYTECODE=1
WORKDIR /sim
