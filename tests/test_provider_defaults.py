"""Upgrade activation must not overwrite later source preferences."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

RESOURCES = Path(__file__).resolve().parents[1] / "src/plugin.video.streamhub/resources"


class ProviderDefaultsTests(unittest.TestCase):
    def setUp(self):
        self.settings = {"movie2k": "false", "kinox": "false", "vavoo": "false"}
        utils = types.ModuleType("streamhub.utils")
        utils.getSetting = lambda key: self.settings.get(key, "")
        utils.setSetting = Mock(side_effect=lambda key, value: self.settings.__setitem__(key, value))
        utils.log = Mock()
        self.utils = utils
        patcher = patch.dict(sys.modules, {"streamhub.utils": utils})
        patcher.start()
        self.addCleanup(patcher.stop)
        spec = importlib.util.spec_from_file_location("provider_defaults_tested", RESOURCES / "streamhub/provider_defaults.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_upgrade_enables_only_requested_sources_once(self):
        self.assertTrue(self.module.apply())
        self.assertEqual(self.settings["movie2k"], "true")
        self.assertEqual(self.settings["kinox"], "true")
        self.assertEqual(self.settings["vavoo"], "false")
        self.settings.update(movie2k="false", kinox="false")
        self.utils.setSetting.reset_mock()
        self.assertFalse(self.module.apply())
        self.utils.setSetting.assert_not_called()
        self.assertEqual(self.settings["movie2k"], "false")
        self.assertEqual(self.settings["kinox"], "false")

    def test_fresh_defaults_only_write_completion_marker(self):
        self.settings.update(movie2k="true", kinox="true")
        self.assertTrue(self.module.apply())
        self.utils.setSetting.assert_called_once_with(self.module.MARKER, "true")

    def test_failed_write_does_not_mark_upgrade_complete(self):
        self.utils.setSetting.side_effect = None
        self.assertFalse(self.module.apply())
        self.assertNotIn(self.module.MARKER, self.settings)
        self.utils.setSetting.side_effect = lambda key, value: self.settings.__setitem__(key, value)
        self.assertTrue(self.module.apply())

    def test_failed_read_does_not_break_startup(self):
        self.utils.getSetting = Mock(side_effect=RuntimeError("unavailable"))
        self.assertFalse(self.module.apply())
        self.utils.setSetting.assert_not_called()
        self.utils.log.assert_called_once()

    def test_setting_exception_does_not_break_startup(self):
        self.utils.setSetting.side_effect = RuntimeError("unavailable")
        self.assertFalse(self.module.apply())
        self.assertNotIn(self.module.MARKER, self.settings)
        self.utils.log.assert_called_once()


if __name__ == "__main__":
    unittest.main()
