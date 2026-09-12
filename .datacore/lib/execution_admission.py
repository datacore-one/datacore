"""Installation-bound, durable admission for a single designated executor.

The administrator provisions this control-plane state outside synced Data.
There is no lease, expiry, release, takeover or automatic database bootstrap.
Workers must run under an independent identity without access to this state;
this module does not itself supply that OS boundary.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import uuid

CONFIG = Path('/etc/datacore/execution-site.json')
STATE = Path('/var/lib/datacore-nightshift')
MACHINE_ID = Path('/etc/machine-id')


class SiteError(ValueError):
    """Execution authority is unavailable, changed or already consumed."""


def installation_id(value):
    if not isinstance(value, str):
        raise SiteError('invalid execution installation identity')
    try:
        valid = str(uuid.UUID(value)) == value
    except ValueError:
        valid = False
    if not valid:
        raise SiteError('invalid execution installation identity')
    return value


def _mapping(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SiteError('ambiguous execution installation configuration')
        result[key] = value
    return result


def _private(path, uid, *, directory=False):
    info = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (not kind(info.st_mode) or info.st_uid != uid or
            stat.S_IMODE(info.st_mode) & 0o077 or
            (not directory and info.st_nlink != 1)):
        raise SiteError('execution admission state is not private to its controller')


@dataclass(frozen=True)
class Site:
    installation: str
    state: Path

    @contextmanager
    def _database(self):
        try:
            _private(self.state, os.geteuid(), directory=True)
            path = self.state / 'admissions.sqlite3'
            _private(path, os.geteuid())
            for suffix in ('-journal', '-wal', '-shm'):
                sidecar = path.with_name(path.name + suffix)
                if os.path.lexists(sidecar):
                    _private(sidecar, os.geteuid())
            # Existing-only: deleting or losing the authority must never reset
            # consumed approvals. Provisioning is a separate admin operation.
            connection = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True,
                                         timeout=10, isolation_level=None)
            try:
                connection.execute('PRAGMA synchronous=FULL')
                connection.execute('PRAGMA trusted_schema=OFF')
                if connection.execute('PRAGMA journal_mode').fetchone()[0] != 'delete':
                    raise SiteError('unsupported execution admission journal mode')
                metadata = connection.execute('SELECT version, installation FROM site').fetchall()
                if metadata != [(1, self.installation)]:
                    raise SiteError('execution admission database belongs to another installation')
                yield connection
            finally:
                connection.close()
        except (OSError, sqlite3.Error):
            raise SiteError('execution admission state unavailable; reconcile before retry') from None

    def consume(self, allocation, owner, token, payload_hash):
        """Durably burn one approval before returning permission to execute."""
        if any(not isinstance(x, str) or not x or len(x) > 512
               for x in (allocation, owner, token, payload_hash)):
            raise SiteError('invalid execution admission identity')
        with self._database() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                db.execute('INSERT INTO admissions VALUES (?, ?, ?, ?)',
                           (allocation, owner, token, payload_hash))
            except sqlite3.IntegrityError:
                db.execute('ROLLBACK')
                raise SiteError('allocation already consumed at this installation; reconcile before retry') from None
            db.execute('COMMIT')

    def owns(self, allocation, owner, token, payload_hash):
        with self._database() as db:
            return db.execute('SELECT owner, token, payload_hash FROM admissions WHERE allocation=?',
                              (allocation,)).fetchone() == (owner, token, payload_hash)


def load() -> Site:
    """Use administrator-owned host configuration, never an environment ID."""
    try:
        for parent in reversed(CONFIG.parents):
            info = parent.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise SiteError('execution installation configuration is not administrator controlled')
        fd = os.open(CONFIG, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
                    or info.st_size > 4096):
                raise SiteError('execution installation configuration is not administrator controlled')
            raw = os.read(fd, 4097)
        finally:
            os.close(fd)
        config = json.loads(raw, object_pairs_hook=_mapping)
        if (not isinstance(config, dict) or set(config) != {'version', 'installation', 'machine_id', 'controller_uid'}
                or type(config['version']) is not int or config['version'] != 1
                or type(config['controller_uid']) is not int or config['controller_uid'] != os.geteuid()
                or not isinstance(config['machine_id'], str)
                or not re.fullmatch('[0-9a-f]{32}', config['machine_id'])
                or MACHINE_ID.read_text().strip() != config['machine_id']):
            raise SiteError('execution installation does not match this host and controller')
        return Site(installation_id(config['installation']), STATE)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise SiteError('execution installation unavailable; administrator provisioning required') from None


def provision_database(directory: Path, installation: str):
    """Create NEW state only, for the installer; never replaces an old store."""
    installation_id(installation)
    directory.mkdir(mode=0o700)
    path = directory / 'admissions.sqlite3'
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.execute('PRAGMA journal_mode=DELETE')
        db.execute('PRAGMA synchronous=FULL')
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE site (version INTEGER NOT NULL, installation TEXT NOT NULL)')
        db.execute('INSERT INTO site VALUES (1, ?)', (installation,))
        db.execute('CREATE TABLE admissions (allocation TEXT PRIMARY KEY NOT NULL, owner TEXT NOT NULL, token TEXT NOT NULL, payload_hash TEXT NOT NULL)')
        db.execute('COMMIT')
    finally:
        db.close()
    for parent in (directory, directory.parent):
        fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def main(argv=None):
    """Provision once as administrator; print only the public installation ID."""
    import argparse
    import pwd
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['provision'])
    parser.add_argument('--controller-user', required=True)
    args = parser.parse_args(argv)
    try:
        if os.geteuid() != 0:
            raise SiteError('provisioning requires the administrator')
        account = pwd.getpwnam(args.controller_user)
        if account.pw_uid == 0:
            raise SiteError('the execution controller must be an unprivileged account')
        if os.path.lexists(CONFIG) or os.path.lexists(STATE):
            raise SiteError('installation already has configuration or state; preserve and reconcile it')
        machine = MACHINE_ID.read_text().strip()
        if not re.fullmatch('[0-9a-f]{32}', machine):
            raise SiteError('host machine identity is unavailable')
        for parent in (CONFIG.parent, STATE.parent):
            parent.mkdir(mode=0o755, exist_ok=True)
            for ancestor in (parent, *parent.parents):
                info = ancestor.lstat()
                if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                    raise SiteError('installation parent is not administrator controlled')
        identity = str(uuid.uuid4())
        provision_database(STATE, identity)
        os.chown(STATE / 'admissions.sqlite3', account.pw_uid, account.pw_gid)
        os.chown(STATE, account.pw_uid, account.pw_gid)
        config = {'version': 1, 'installation': identity, 'machine_id': machine,
                  'controller_uid': account.pw_uid}
        fd = os.open(CONFIG, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o644)
        try:
            # Provisioning commonly runs with umask 077; the controller must
            # still be able to read this non-secret, root-owned declaration.
            os.fchmod(fd, 0o644)
            raw = (json.dumps(config, sort_keys=True) + '\n').encode()
            with os.fdopen(fd, 'wb', closefd=False) as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(fd)
        finally:
            os.close(fd)
        fd = os.open(CONFIG.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        print(json.dumps({'installation': identity, 'controller_uid': account.pw_uid}))
        return 0
    except (OSError, KeyError, ValueError, sqlite3.Error):
        print('Execution installation provisioning failed; existing or partial state was preserved.',
              file=__import__('sys').stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
