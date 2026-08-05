from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prazycron.i18n import STRINGS, Translator
from prazycron.config import DEFAULTS, load_config


class LocalizationTests(unittest.TestCase):
    def test_polish_catalog_covers_every_english_key(self) -> None:
        self.assertEqual(set(STRINGS["en"]), set(STRINGS["pl"]))

    def test_extended_polish_interface_labels(self) -> None:
        tr = Translator("pl")
        expected = {
            "refresh": "Odśwież",
            "run_now": "Uruchom teraz",
            "next_runs": "Następne uruchomienia",
            "execution_history": "Historia wykonania",
            "diagnostics": "Diagnostyka",
            "table_columns": "Kolumny tabeli",
            "tui_cron_service": "Usługa Cron",
        }
        for key, value in expected.items():
            self.assertEqual(tr(key), value)

    def test_default_interface_is_english_and_dark(self) -> None:
        self.assertEqual(DEFAULTS["language"], "en")
        self.assertEqual(DEFAULTS["theme"], "dark")

    def test_legacy_automatic_language_migrates_to_english(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Path(tmp) / "settings.json"
            settings.write_text(json.dumps({"language": "pl", "language_explicit": False, "theme": "dark"}), encoding="utf-8")
            with patch("prazycron.config.CONFIG_FILE", settings):
                cfg = load_config()
        self.assertEqual(cfg["language"], "en")

    def test_explicit_language_choice_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Path(tmp) / "settings.json"
            settings.write_text(json.dumps({"language": "pl", "language_explicit": True, "theme": "dark"}), encoding="utf-8")
            with patch("prazycron.config.CONFIG_FILE", settings):
                cfg = load_config()
        self.assertEqual(cfg["language"], "pl")


if __name__ == "__main__":
    unittest.main()
