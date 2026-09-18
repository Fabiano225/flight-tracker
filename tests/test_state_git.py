"""Real local Git round-trips; no internet, account, or API credentials."""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from tracker.store import Store

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "state_git.py"


class GitStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.remote = self.root / "remote.git"
        self.a = self.root / "a"
        self.b = self.root / "b"
        self.command(["git", "init", "--bare", str(self.remote)], self.root)
        for repo in (self.a, self.b):
            repo.mkdir()
            self.command(["git", "init", "-b", "main"], repo)
            self.command(["git", "remote", "add", "origin", str(self.remote)], repo)

    def tearDown(self):
        self.temp.cleanup()

    def command(self, argv, cwd, success=True):
        result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def state(self, repo, action, success=True):
        return self.command([sys.executable, str(SCRIPT), action], repo, success)

    def seed(self, repo, label="first", mode="live"):
        folder = repo / "state"
        with Store(folder, mode) as store:
            store.set_meta("test-label", label)
        for name in ("latest.json", "latest.csv", "report.md"):
            (folder / name).write_text(label, encoding="utf-8")

    def test_first_run_save_restore_and_fast_forward(self):
        self.state(self.a, "restore")
        self.seed(self.a)
        self.state(self.a, "save")
        first = (self.a / "state" / ".state-parent").read_text()
        self.state(self.a, "save")
        self.assertEqual((self.a / "state" / ".state-parent").read_text(), first)
        self.state(self.b, "restore")
        self.assertEqual((self.b / "state" / "history.sqlite3").read_bytes(),
                         (self.a / "state" / "history.sqlite3").read_bytes())
        self.seed(self.a, "second")
        self.state(self.a, "save")
        self.assertNotEqual((self.a / "state" / ".state-parent").read_text(), first)

    def test_concurrent_writer_is_rejected_without_lost_history(self):
        self.state(self.a, "restore")
        self.seed(self.a)
        self.state(self.a, "save")
        self.state(self.b, "restore")
        self.seed(self.a, "new-head")
        self.state(self.a, "save")
        self.seed(self.b, "stale-writer")
        self.state(self.b, "save", success=False)
        remote = self.command(["git", "ls-remote", "origin", "refs/heads/tracker-state"], self.a).stdout.split()[0]
        self.assertEqual(remote, (self.a / "state" / ".state-parent").read_text())

    def test_demo_state_never_published(self):
        self.state(self.a, "restore")
        self.seed(self.a, mode="demo")
        self.state(self.a, "save", success=False)

    def test_corrupt_database_never_published(self):
        self.state(self.a, "restore")
        self.seed(self.a)
        (self.a / "state" / "history.sqlite3").write_bytes(b"not sqlite")
        self.state(self.a, "save", success=False)

    def test_restore_preserves_existing_local_state(self):
        self.seed(self.a)
        before = (self.a / "state" / "history.sqlite3").read_bytes()
        self.state(self.a, "restore", success=False)
        self.assertEqual((self.a / "state" / "history.sqlite3").read_bytes(), before)

    def test_missing_remote_is_not_treated_as_empty_history(self):
        self.command(["git", "remote", "set-url", "origin", str(self.root / "absent.git")], self.a)
        self.state(self.a, "restore", success=False)
        self.assertFalse((self.a / "state" / ".state-parent").exists())


if __name__ == "__main__":
    unittest.main()
