from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from prazycron.main import gui_available


class MainTests(unittest.TestCase):
    def test_gui_detection(self) -> None:
        with patch.dict(os.environ, {"DISPLAY": ":1"}, clear=True):
            self.assertTrue(gui_available())
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(gui_available())


if __name__ == "__main__":
    unittest.main()
