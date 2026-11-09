"""A disposable credential fleet for OPS-6 / OPS-7 evals.

Builds, entirely under tmp_path:
  secrets/            a copy of the real distribute.sh / sync.sh / scoped_index.py
                      with FIXTURE values in global.env and one manifest per host
  root/.datacore/     the broker files distribute.sh ships (copied from lib/)
  hosts/<alias>/      each fake host's HOME; ~/Data/.datacore/... lives inside
  bin/ssh, bin/scp    fakes that run the remote command locally with HOME set to
                      the host dir, and log every remote command

No real secret is read: only the scripts are copied, and every value is a
fixture string written here.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
DC = LIB.parent
SCRIPTS = DC / "secrets" / "scripts"
BROKER_LIBS = ("credential_access.py", "creds.py", "CREDENTIALS.md", "secret_http.py",
               "relay_client.py", "env_utils.py")

FAKE_SSH = r'''#!/bin/bash
# fake ssh: skip options, take the destination, run the rest locally as that host
while [[ "$1" == -* ]]; do case "$1" in -o|-i|-p|-l) shift 2;; *) shift;; esac; done
dest="${1#*@}"; shift
echo "$dest: $*" >> "$FAKE_FLEET/ssh.log"
h="$FAKE_FLEET/hosts/$dest"; [ -d "$h" ] || { echo "ssh: $dest unreachable" >&2; exit 255; }
cd "$h" && env -u DATACORE_ROOT HOME="$h" bash -c "$*"
'''
FAKE_SCP = r'''#!/bin/bash
while [[ "$1" == -* ]]; do case "$1" in -o|-i|-P) shift 2;; *) shift;; esac; done
src="$1"; dst="$2"; host="${dst%%:*}"; host="${host#*@}"; path="${dst#*:}"
h="$FAKE_FLEET/hosts/$host"; [ -d "$h" ] || exit 1
case "$path" in /*) exit 1;; esac
mkdir -p "$(dirname "$h/$path")" && cp "$src" "$h/$path"
'''


def fp(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:12]


class Fleet:
    def __init__(self, tmp: Path, hosts=("hosta", "hostb")):
        self.tmp = tmp
        self.hosts = list(hosts)
        self.secrets = tmp / "secrets"
        (self.secrets / "scripts").mkdir(parents=True)
        for f in ("distribute.sh", "sync.sh", "scoped_index.py"):
            shutil.copy2(SCRIPTS / f, self.secrets / "scripts" / f)
        (self.secrets / "instances").mkdir()
        (self.secrets / ".instance").write_text("self\n")
        (self.secrets / "instances" / "self.yaml").write_text("spaces: []\n")
        for h in self.hosts:
            (self.secrets / "instances" / f"{h}.yaml").write_text(
                f"ssh_alias: {h}\nspaces: []\n")
            hl = tmp / "hosts" / h / "Data" / ".datacore" / "lib"
            hl.mkdir(parents=True)
            for f in ("secret_http.py", "relay_client.py", "env_utils.py"):
                shutil.copy2(LIB / f, hl / f)
            cfg = tmp / "hosts" / h / "Data" / ".datacore" / "config"
            cfg.mkdir(parents=True)
            shutil.copy2(DC / "config" / "credential-stores.yaml", cfg / "credential-stores.yaml")
        self.root = tmp / "root"
        (self.root / ".datacore" / "lib").mkdir(parents=True)
        (self.root / ".datacore" / "config").mkdir(parents=True)
        for f in BROKER_LIBS:
            if (LIB / f).exists():
                shutil.copy2(LIB / f, self.root / ".datacore" / "lib" / f)
        shutil.copy2(DC / "config" / "credential-stores.yaml",
                     self.root / ".datacore" / "config" / "credential-stores.yaml")
        self.bin = tmp / "bin"
        self.bin.mkdir()
        for name, body in (("ssh", FAKE_SSH), ("scp", FAKE_SCP)):
            (self.bin / name).write_text(body)
            (self.bin / name).chmod(0o755)

    def central(self, values: dict[str, str]) -> None:
        (self.secrets / "global.env").write_text(
            "".join(f"{k}={v}\n" for k, v in values.items()))

    def host_file(self, host: str, rel: str) -> Path:
        """rel is relative to the host's HOME, e.g. '.config/cos.env'."""
        return self.tmp / "hosts" / host / rel

    def write_store(self, host: str, rel: str, values: dict[str, str]) -> None:
        p = self.host_file(host, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("".join(f"{k}={v}\n" for k, v in values.items()))

    def stores(self, host: str) -> dict[str, dict[str, str]]:
        """{store path relative to HOME: {VAR: fingerprint}} for Datacore-owned stores."""
        out = {}
        h = self.tmp / "hosts" / host
        for rel in ("Data/.datacore/env/.env", "Data/.datacore/env/local.env",
                    ".config/cos.env", ".datacore/datacore.env"):
            p = h / rel
            if not p.is_file():
                continue
            vals = {}
            for line in p.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    vals[k.strip()] = fp(v.strip())
            out[rel] = vals
        return out

    def distribute(self) -> subprocess.CompletedProcess:
        env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}",
                   FAKE_FLEET=str(self.tmp), DATACORE_ROOT=str(self.root), HOME=str(self.tmp / "home"))
        (self.tmp / "home").mkdir(exist_ok=True)
        return subprocess.run(["bash", str(self.secrets / "scripts" / "distribute.sh")],
                              env=env, capture_output=True, text=True, timeout=55)

    def ssh_log(self) -> str:
        p = self.tmp / "ssh.log"
        return p.read_text() if p.exists() else ""
