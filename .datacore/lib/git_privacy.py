"""Read the exact Git objects being validated, without shell/path ambiguity."""
import re
import subprocess
from pathlib import Path

from context_merge import PRIVATE_PATTERNS


def git_bytes(repo: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True)
    if result.returncode:
        raise ValueError(f"Git {args[0]} failed; validation cannot continue")
    return result.stdout


def commit_id(repo: Path, revision: str) -> str:
    raw = git_bytes(repo, "rev-parse", "--verify", "--end-of-options", revision + "^{commit}").strip()
    if not re.fullmatch(rb"[0-9a-f]{40,64}", raw):
        raise ValueError("invalid commit object")
    return raw.decode("ascii")


def changed_paths(repo: Path, revision: str) -> tuple[set[str], set[str]]:
    """Include root commits, renames as delete+add, and every merge parent."""
    if revision == ":index":
        raw = git_bytes(repo, "diff", "--cached", "--no-renames", "--name-status", "-z")
    else:
        revision = commit_id(repo, revision)
        raw = git_bytes(repo, "diff-tree", "--no-commit-id", "--root", "-r", "-m",
                        "--no-renames", "--name-status", "-z", revision)
    fields = raw.split(b"\0")
    if fields[-1] == b"":
        fields.pop()
    if len(fields) % 2:
        raise ValueError("malformed Git path output")
    added, modified = set(), set()
    for status, raw_path in zip(fields[::2], fields[1::2]):
        path = raw_path.decode("utf-8", "surrogateescape")
        if status == b"A":
            added.add(path)
        elif status in (b"M", b"T"):
            modified.add(path)
        elif status != b"D":
            raise ValueError("unexpected Git change status")
    return added, modified


def tree_entries(repo: Path, revision: str):
    if revision == ":index":
        revision = git_bytes(repo, "write-tree").strip().decode("ascii")
    revision = git_bytes(repo, "rev-parse", "--verify", "--end-of-options", revision + "^{tree}").strip().decode("ascii")
    for row in git_bytes(repo, "ls-tree", "-rz", "--full-tree", revision).split(b"\0"):
        if row:
            metadata, path = row.split(b"\t", 1)
            mode, kind, oid = metadata.split()
            yield mode.decode(), kind.decode(), oid.decode(), path.decode("utf-8", "surrogateescape")


def validate_public_tree(repo: Path, revision: str) -> list[str]:
    """Validate all public-layer blobs and gitlinks in the committed tree."""
    entries = list(tree_entries(repo, revision))
    problems = []
    registered = set()
    for mode, kind, oid, path in entries:
        if path == ".gitmodules" and kind == "blob":
            # Parse Git's actual configuration format, not whitespace guesses.
            result = subprocess.run(["git", "-C", str(repo), "config", "--blob", oid,
                                     "--null", "--get-regexp", r"^submodule\..*\.path$"], capture_output=True)
            if result.returncode not in (0, 1):
                raise ValueError("invalid .gitmodules")
            for record in result.stdout.split(b"\0"):
                if record:
                    registered.add(record.split(b"\n", 1)[1].decode("utf-8", "surrogateescape"))
    for mode, kind, oid, path in entries:
        if mode == "160000" and path not in registered:
            problems.append(f"unregistered gitlink: {path!r}")
        if path.endswith(".base.md"):
            if mode not in ("100644", "100755"):
                problems.append(f"public layer must be a regular file: {path!r}")
                continue
            content = git_bytes(repo, "cat-file", "blob", oid).decode("utf-8")
            for pattern, description in PRIVATE_PATTERNS:
                if re.search(pattern, content):
                    problems.append(f"{path!r}: potential {description}")
    return problems


def outgoing_commits(repo: Path, remote_name: str, lines) -> set[str]:
    """Resolve every outgoing commit, with no truncation or error fallback."""
    revisions = set()
    for line in lines:
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 4:
            raise ValueError("invalid pre-push input")
        _, local, _, remote = fields
        if not re.fullmatch(r"[0-9a-f]{40,64}", local) or not re.fullmatch(r"[0-9a-f]{40,64}", remote):
            raise ValueError("invalid pre-push object ID")
        if not local.strip("0"):
            continue
        local = commit_id(repo, local)
        if remote.strip("0"):
            remote = commit_id(repo, remote)
            outgoing = git_bytes(repo, "rev-list", remote + ".." + local)
        else:
            outgoing = git_bytes(repo, "rev-list", local, "--not", "--remotes=" + remote_name)
        revisions.update(outgoing.decode().splitlines())
        revisions.add(local)
    return revisions


def main(argv=None):
    import argparse
    import sys
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--revision", default="HEAD")
    modes.add_argument("--index", action="store_true")
    modes.add_argument("--pre-push", metavar="REMOTE")
    modes.add_argument("--outgoing", metavar="REMOTE")
    args = parser.parse_args(argv)
    try:
        if args.pre_push or args.outgoing:
            revisions = outgoing_commits(args.repo, args.pre_push or args.outgoing, sys.stdin)
            if args.outgoing:
                for revision in sorted(revisions):
                    print(revision)
                return 0
        elif args.index:
            revisions = [git_bytes(args.repo, "write-tree").strip().decode("ascii")]
        else:
            revisions = [args.revision]
        problems = []
        for revision in sorted(revisions):
            problems.extend(validate_public_tree(args.repo, revision))
    except (ValueError, OSError, UnicodeError) as exc:
        print(f"Validation failed: {exc}")
        return 2
    for problem in problems:
        print(problem)
    return int(bool(problems))


if __name__ == "__main__":
    raise SystemExit(main())
