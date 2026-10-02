"""A copy of the project (a fork) links to itself and runs without notification secrets."""
from datetime import date
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from scripts import setup_check
from scripts.build_site import build
from tracker import project
from tracker.config import Config
from tracker.notifications import notifications_configured

ROOT = Path(__file__).resolve().parents[1]
CLEAN = {name: "" for name in ("GITHUB_REPOSITORY", "DASHBOARD_URL", "NOTIFICATION_CHANNEL", "DISCORD_WEBHOOK_URL",
                               "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")}


class ProjectTests(unittest.TestCase):
    def test_repository_comes_from_actions_then_git(self):
        with mock.patch.dict(os.environ, {**CLEAN, "GITHUB_REPOSITORY": "someone/my-flights"}):
            self.assertEqual(project.repository(), "someone/my-flights")
            self.assertEqual(project.repository_url(), "https://github.com/someone/my-flights")
        for url in ("https://github.com/Owner/repo.git", "git@github.com:Owner/repo.git", "http://proxy/git/Owner/repo"):
            with mock.patch.dict(os.environ, CLEAN), mock.patch.object(project.subprocess, "run") as run:
                run.return_value.stdout = url + "\n"
                self.assertEqual(project.repository(), "Owner/repo", url)
        with mock.patch.dict(os.environ, {**CLEAN, "GITHUB_REPOSITORY": "not a repo"}), \
                mock.patch.object(project, "from_git", return_value=""):
            self.assertEqual(project.repository(), project.FALLBACK)

    def test_dashboard_address_follows_the_repository(self):
        def url(**env):
            with mock.patch.dict(os.environ, {**CLEAN, **env}):
                return project.dashboard_url()
        self.assertEqual(url(GITHUB_REPOSITORY="Someone/my-flights"), "https://someone.github.io/my-flights/")
        self.assertEqual(url(GITHUB_REPOSITORY="Someone/someone.github.io"), "https://someone.github.io/")
        self.assertEqual(url(GITHUB_REPOSITORY="a/b", DASHBOARD_URL="https://flights.example.org"),
                         "https://flights.example.org/")
        # Anything but a plain https address is ignored.
        self.assertEqual(url(GITHUB_REPOSITORY="a/b", DASHBOARD_URL="javascript:alert(1)"), "https://a.github.io/b/")

    def test_website_links_to_the_repository_it_is_built_from(self):
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.dict(os.environ, {**CLEAN, "GITHUB_REPOSITORY": "someone/my-flights"}):
            # A new copy has no state yet: the dashboard is published empty.
            with self.assertRaises(Exception):  # Missing state is an error unless declared empty.
                build(Path(temp) / "missing.sqlite3", Path(temp) / "unused")
            build(Path(temp) / "missing.sqlite3", Path(temp) / "site", empty=True)
            for name in ("index.html", "settings.html"):
                page = (Path(temp) / "site" / name).read_text(encoding="utf-8")
                self.assertNotIn("{{", page)
                self.assertNotIn("Fabiano225", page)
                self.assertIn('href="https://github.com/someone/my-flights"', page)
            data = json.loads((Path(temp) / "site" / "data.json").read_text(encoding="utf-8"))
            self.assertEqual((data["trips"][0]["scan"], data["trips"][0]["offers"]), (None, []))
            form = json.loads((Path(temp) / "site" / "search-config.json").read_text(encoding="utf-8"))
            self.assertEqual(form["repository"], "someone/my-flights")


class NotificationSetupTests(unittest.TestCase):
    def test_runs_without_any_channel_skip_delivery(self):
        with mock.patch.dict(os.environ, CLEAN):
            self.assertFalse(notifications_configured())
        for env in ({"DISCORD_WEBHOOK_URL": "x"}, {"TELEGRAM_BOT_TOKEN": "x"}, {"NOTIFICATION_CHANNEL": "discord"}):
            with mock.patch.dict(os.environ, {**CLEAN, **env}):
                self.assertTrue(notifications_configured(), env)
        with tempfile.TemporaryDirectory() as temp:
            result = subprocess.run([sys.executable, "-m", "tracker", "notify", "--state-dir", temp],
                                    cwd=ROOT, capture_output=True, text=True, env={**os.environ, **CLEAN})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("No Discord or Telegram set up", result.stdout)


class SetupCheckTests(unittest.TestCase):
    def facts(self, **changes):
        trip = Config.load(ROOT / "config.json")
        return {"repo": {"has_issues": True}, "pages": {"build_type": "workflow", "html_url": "https://a.github.io/b/"},
                "state": True, "trips": [trip], "config_error": None, "discord": True, "telegram_token": False,
                "telegram_chat": False, "channel": "", "paused": False, **changes}

    def rows(self, **changes):
        with mock.patch.dict(os.environ, {**CLEAN, "GITHUB_REPOSITORY": "a/b"}):
            return setup_check.checks(self.facts(**changes), date(2026, 10, 2))

    def test_a_complete_setup_needs_nothing(self):
        rows = self.rows()
        self.assertTrue(all(status == setup_check.OK for status, _, _ in rows), rows)
        self.assertIn("Everything needed is set up.", setup_check.report(rows))

    def test_each_missing_step_says_what_to_do(self):
        cases = {
            "Pages": dict(pages=None), "Pages ": dict(pages={"build_type": "legacy"}),
            "Issues": dict(repo={"has_issues": False}), "Run workflow": dict(state=False),
            "half set up": dict(discord=False, telegram_token=True),
            "not set up": dict(discord=False, channel="telegram"), "invalid": dict(config_error="Bad", trips=[]),
        }
        for text, changes in cases.items():
            rows = self.rows(**changes)
            todo = [row for row in rows if row[0] == setup_check.TODO]
            self.assertEqual(len(todo), 1, (text, rows))
            self.assertIn(text.strip(), " ".join(todo[0]))
            self.assertIn("1 step left", setup_check.report(rows))

    def test_optional_and_paused_states_are_notes(self):
        rows = self.rows(discord=False, paused=True, trips=[Config(departure_start="2026-09-01",
                                                                    departure_end="2026-10-01")])
        notes = [title for status, title, _ in rows if status == setup_check.NOTE]
        self.assertEqual(len(notes), 3, rows)
        self.assertFalse([row for row in rows if row[0] == setup_check.TODO])


if __name__ == "__main__":
    unittest.main()
