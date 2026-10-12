"""Regression tests with fake Kodi APIs; never read or modify a Kodi profile."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

SOURCE = Path(__file__).resolve().parents[1] / "src/plugin.video.streamhub/resources/streamhub/buffer.py"


class BufferTests(unittest.TestCase):
    def setUp(self):
        self.profile = tempfile.TemporaryDirectory()
        self.addCleanup(self.profile.cleanup)
        self.settings = {}
        self.values = {"memorysize": 20, "readfactor": 400, "buffermode": 4}
        self.initial = dict(self.values)
        self.version = "21.3 (21.3.0)"
        self.calls = []
        self.reject = None
        self.utils = types.ModuleType("streamhub.utils")
        self.utils.getSetting = lambda key: self.settings.get(key, "")
        self.utils.setSetting = lambda key, value: self.settings.__setitem__(key, value)
        self.utils.translatePath = lambda path: self.profile.name
        self.utils.format_exc = lambda: "test traceback"
        self.utils.log = Mock()
        self.utils.dialog = Mock()
        self.utils.xbmc = types.SimpleNamespace(
            getInfoLabel=lambda _: self.version, executeJSONRPC=self.rpc)
        with patch.dict(sys.modules, {"streamhub.utils": self.utils}):
            spec = importlib.util.spec_from_file_location("buffer_under_test", SOURCE)
            self.buffer = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.buffer)

    @property
    def xml(self):
        return Path(self.profile.name) / "advancedsettings.xml"

    def rpc(self, raw):
        request = json.loads(raw)
        self.calls.append(request)
        method = request["method"]
        params = request["params"]
        key = params["setting"].removeprefix("filecache.")
        if params["setting"] != "filecache." + key or key not in self.values:
            return json.dumps({"error": {"code": -32602}})
        if method == "Settings.GetSettingValue":
            result = {"value": self.values[key]}
        elif method == "Settings.SetSettingValue":
            if self.reject == (key, params["value"]):
                result = False
            else:
                self.values[key] = params["value"]
                result = True
        else:
            return json.dumps({"error": {"code": -32601}})
        return json.dumps({"result": result})

    def test_modern_uses_valid_mib_units_and_adaptive_readfactor(self):
        self.assertTrue(self.buffer.apply("1"))
        self.assertEqual(self.values, {"memorysize": 64, "readfactor": 0, "buffermode": 2})
        self.assertFalse(self.xml.exists())
        self.assertEqual(self.buffer.current()["memorysize"], str(64 * 1024 * 1024))
        self.assertEqual(self.buffer.current()["readfactor"], "adaptiv")

    def test_new_off_preserves_user_configuration(self):
        self.settings["buffer_size"] = "0"
        self.xml.write_text("<advancedsettings><cache><memorysize>999</memorysize></cache></advancedsettings>")
        original_xml = self.xml.read_bytes()
        self.assertTrue(self.buffer.apply_if_changed())
        self.assertEqual(self.values, self.initial)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.xml.read_bytes(), original_xml)

    def test_disabling_restores_original_values(self):
        self.assertTrue(self.buffer.apply("1"))
        self.assertTrue(self.buffer.apply("2"))
        self.assertTrue(self.buffer.apply("0"))
        self.assertEqual(self.values, self.initial)
        self.assertEqual(json.loads(self.settings["_buffer_backup"]), {})

    def test_disabling_keeps_manual_changes(self):
        self.assertTrue(self.buffer.apply("1"))
        self.values["memorysize"] = 192
        self.assertTrue(self.buffer.apply("0"))
        self.assertEqual(self.values, dict(self.initial, memorysize=192))

    def test_changing_preset_remembers_later_manual_settings(self):
        self.assertTrue(self.buffer.apply("1"))
        self.values["memorysize"] = 192
        self.assertTrue(self.buffer.apply("2"))
        self.assertTrue(self.buffer.apply("0"))
        self.assertEqual(self.values, dict(self.initial, memorysize=192))

    def test_rpc_rejection_rolls_back_and_does_not_mark_success(self):
        self.settings["buffer_size"] = "1"
        self.reject = ("readfactor", 0)
        self.assertFalse(self.buffer.apply_if_changed())
        self.assertEqual(self.values, self.initial)
        self.assertNotIn("_buffer_applied", self.settings)

    def test_failed_preset_change_keeps_original_backup(self):
        self.assertTrue(self.buffer.apply("1"))
        self.values["readfactor"] = 400
        backup = self.settings["_buffer_backup"]
        self.reject = ("readfactor", 0)
        self.assertFalse(self.buffer.apply("2"))
        self.assertEqual(self.settings["_buffer_backup"], backup)
        self.reject = None
        self.assertTrue(self.buffer.apply("0"))
        self.assertEqual(self.values, self.initial)

    def test_old_applied_marker_is_migrated_once(self):
        self.settings.update(buffer_size="2", _buffer_applied="2")
        self.assertTrue(self.buffer.apply_if_changed())
        self.assertEqual(self.values["memorysize"], 128)
        self.assertEqual(self.settings["_buffer_applied"], "2:kodi21:2")
        self.calls.clear()
        self.assertFalse(self.buffer.apply_if_changed())
        self.assertEqual(self.calls, [])

    def test_unknown_version_does_not_change_settings(self):
        self.version = ""
        self.assertFalse(self.buffer.apply_if_changed())
        self.assertFalse(self.buffer.apply("1"))
        self.assertEqual(self.calls, [])
        self.assertFalse(self.xml.exists())

    def test_legacy_preserves_extra_xml_and_restores_only_owned_keys(self):
        self.version = "20.5"
        self.xml.write_text("<advancedsettings><!--keep--><network><curlclienttimeout>15</curlclienttimeout></network><cache><chunksize>65536</chunksize><memorysize>20971520</memorysize></cache></advancedsettings>")
        self.assertTrue(self.buffer.apply("1"))
        root = ET.parse(self.xml).getroot()
        self.assertEqual(root.findtext("cache/memorysize"), str(64 * 1024 * 1024))
        self.assertEqual(root.findtext("cache/readfactor"), "4")
        self.assertEqual(root.findtext("cache/buffermode"), "2")
        self.assertTrue(self.buffer.apply("0"))
        root = ET.parse(self.xml).getroot()
        self.assertEqual(root.findtext("cache/memorysize"), "20971520")
        self.assertEqual(root.findtext("cache/chunksize"), "65536")
        self.assertIsNone(root.find("cache/readfactor"))
        self.assertEqual(root.findtext("network/curlclienttimeout"), "15")
        self.assertIn("<!--keep-->", self.xml.read_text())
        self.assertEqual(self.calls, [])

    def test_legacy_off_without_backup_preserves_existing_cache(self):
        self.version = "20.5"
        self.xml.write_text("<advancedsettings><cache><memorysize>999</memorysize><chunksize>65536</chunksize></cache></advancedsettings>")
        original = self.xml.read_bytes()
        self.assertTrue(self.buffer.apply("0"))
        self.assertEqual(self.xml.read_bytes(), original)

    def test_unreadable_legacy_file_is_not_overwritten(self):
        self.version = "20.5"
        self.xml.write_text("<advancedsettings><broken")
        original = self.xml.read_bytes()
        self.assertFalse(self.buffer.apply("1"))
        self.assertEqual(self.xml.read_bytes(), original)

    def test_kodi_upgrade_applies_correct_backend(self):
        self.version = "20.5"
        self.settings["buffer_size"] = "1"
        self.assertTrue(self.buffer.apply_if_changed())
        original = self.xml.read_bytes()
        self.version = "21.3"
        self.assertTrue(self.buffer.apply_if_changed())
        self.assertEqual(self.values["memorysize"], 64)
        self.assertEqual(self.xml.read_bytes(), original)

    def test_status_reports_modern_settings(self):
        self.assertTrue(self.buffer.apply("1"))
        self.buffer.show_status()
        title, message = self.utils.dialog.ok.call_args.args
        self.assertIn("Kodi-Einstellungen", message)
        self.assertIn("64 MB", message)
        self.assertIn("adaptiv", message)
        self.assertNotIn("advancedsettings.xml", message)

    def test_default_matches_low_ram_preset_in_ui(self):
        self.assertTrue(self.buffer.apply_if_changed())
        self.assertEqual(self.values["memorysize"], 64)
        settings = ET.parse(SOURCE.parents[1] / "settings.xml")
        self.assertEqual(settings.findtext(".//setting[@id='buffer_size']/default"), "1")


if __name__ == "__main__":
    unittest.main()
