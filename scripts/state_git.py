"""Persist only an allowlisted state tree on a separate branch.

Saves are normal fast-forward pushes. Every COMPACT_AFTER saves the branch is
replaced by a single commit (a force push with lease on the restored commit),
so the history of database snapshots cannot grow without bound.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

BRANCH = "refs/heads/tracker-state"
FILES = ("history.sqlite3", "latest.json", "latest.csv", "report.md")
COMPACT_AFTER = 30  # About two to three days of runs.
MAX_FILE_BYTES = 95_000_000  # GitHub rejects files over 100 MB.
COUNTER = "State-Commits: "
MESSAGE = "Update flight history and alert delivery state\n\n"


def git(*args, data=None, check=True):
    result = subprocess.run(["git", *args], input=data, capture_output=True)
    if check and result.returncode:
        raise RuntimeError(f"Git {args[0]} failed; check repository access, branch rules or concurrent writers")
    return result


def validate(path):
    db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("State database failed integrity check; preserve it for recovery")
        mode = db.execute("SELECT value FROM meta WHERE key='mode'").fetchone()
        if not mode or mode[0] != "live":
            raise RuntimeError("Only live databases may be published to tracker-state")
    finally:
        db.close()


def restore(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if any((directory / name).exists() for name in FILES):
        raise RuntimeError("Restore requires an empty state directory; preserve local files first")
    remote = git("ls-remote", "--exit-code", "--heads", "origin", BRANCH, check=False)
    if remote.returncode == 2:
        parent = ""
    elif remote.returncode == 0:
        git("fetch", "--depth=1", "origin", BRANCH)
        parent = git("rev-parse", "FETCH_HEAD").stdout.decode().strip()
        for name in FILES:
            value = git("show", f"{parent}:{name}", check=False)
            if value.returncode:
                raise RuntimeError(f"State branch is missing {name}; refusing to reset history")
            (directory / name).write_bytes(value.stdout)
        validate(directory / "history.sqlite3")
    else:
        raise RuntimeError("State remote lookup failed; refusing to start with empty history")
    (directory / ".state-parent").write_text(parent, encoding="ascii")
    print("Persistent state restored" if parent else "First run: state branch will be created")


def save(directory):
    directory = Path(directory)
    marker = directory / ".state-parent"
    if not marker.exists():
        raise RuntimeError("Run restore successfully before save")
    parent = marker.read_text(encoding="ascii").strip()
    validate(directory / "history.sqlite3")
    entries = []
    for name in sorted(FILES):
        path = directory / name
        if not path.is_file():
            raise RuntimeError(f"Missing state file {name}")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise RuntimeError(f"State file {name} is too large for GitHub; prune the database before saving")
        blob = git("hash-object", "-w", "--stdin", data=path.read_bytes()).stdout.decode().strip()
        entries.append(f"100644 blob {blob}\t{name}\n")
    tree = git("mktree", data="".join(entries).encode()).stdout.decode().strip()
    if parent and git("rev-parse", f"{parent}^{{tree}}").stdout.decode().strip() == tree:
        print("Persistent state unchanged")
        return
    count = commits_since_compaction(parent)
    if parent and (count is None or count >= COMPACT_AFTER):
        # Replace the branch only if it still points at the restored commit.
        single = commit_tree(tree, None, 1)
        if not git("push", f"--force-with-lease={BRANCH}:{parent}", "origin", f"{single}:{BRANCH}",
                   check=False).returncode:
            marker.write_text(single, encoding="ascii")
            print("Persistent state saved; branch history compacted")
            return
        print("History compaction was refused; saving normally")
    commit = commit_tree(tree, parent, (count or 0) + 1)
    git("push", "origin", f"{commit}:{BRANCH}")  # Normal fast-forward only; conflicts fail closed.
    marker.write_text(commit, encoding="ascii")
    print("Persistent state saved")


def commits_since_compaction(parent):
    """Read the counter of the restored commit; None for unknown (older) commits."""
    if not parent:
        return 0
    message = git("log", "-1", "--format=%B", parent).stdout.decode()
    for line in message.splitlines():
        if line.startswith(COUNTER) and line[len(COUNTER):].strip().isdigit():
            return int(line[len(COUNTER):].strip())
    return None


def commit_tree(tree, parent, count):
    args = ["-c", "user.name=github-actions[bot]", "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
            "commit-tree", tree]
    if parent:
        args.extend(["-p", parent])
    return git(*args, data=f"{MESSAGE}{COUNTER}{count}\n".encode()).stdout.decode().strip()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["restore", "save"])
    parser.add_argument("--state-dir", default="state")
    args = parser.parse_args()
    try:
        (restore if args.command == "restore" else save)(args.state_dir)
    except (RuntimeError, OSError, sqlite3.DatabaseError) as exc:
        print(f"State persistence error: {exc}", file=sys.stderr)
        sys.exit(1)
