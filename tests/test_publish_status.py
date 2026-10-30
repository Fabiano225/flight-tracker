"""Messages about publishing the dashboard: one when it starts failing, one when it works again."""
import os
import unittest
from unittest.mock import Mock, patch

from scripts.publish_status import FAILED, RECOVERED, main, message
from tracker.discord import payload_for
from tracker.network import ServiceError


class PublishStatusTests(unittest.TestCase):
    def test_only_a_change_is_reported(self):
        self.assertTrue(message("failure", "success", "123").startswith(FAILED))
        self.assertTrue(message("failure", "").startswith(FAILED))  # Unknown: rather say it.
        self.assertIsNone(message("failure", "failure"))
        self.assertTrue(message("success", "failure").startswith(RECOVERED))
        self.assertIsNone(message("success", "success"))
        self.assertIsNone(message("success", ""))

    def test_failure_links_the_run_only_with_a_numeric_id(self):
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "someone/tracker"}):
            self.assertIn("https://github.com/someone/tracker/actions/runs/123", message("failure", "success", "123"))
            self.assertNotIn("Details", message("failure", "success", "1; rm"))

    def test_sends_once_and_reports_a_broken_channel(self):
        sender = Mock()
        self.assertEqual(main(["--result", "failure", "--previous", "success"], sender), 0)
        self.assertTrue(sender.send.call_args.args[0].startswith(FAILED))
        sender = Mock()
        self.assertEqual(main(["--result", "failure", "--previous", "failure"], sender), 0)
        sender.send.assert_not_called()
        sender = Mock(send=Mock(side_effect=ServiceError("Discord did not acknowledge delivery")))
        self.assertEqual(main(["--result", "success", "--previous", "failure"], sender), 1)

    def test_without_a_channel_nothing_is_sent(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(main(["--result", "failure", "--previous", "success"]), 0)

    def test_discord_titles(self):
        self.assertEqual(payload_for(message("failure", "success"))["embeds"][0]["title"], "⚠️ Dashboard not updated")
        self.assertEqual(payload_for(message("success", "failure"))["embeds"][0]["title"], "✅ Dashboard updated again")


if __name__ == "__main__":
    unittest.main()
