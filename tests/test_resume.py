"""Resume identity, persistence, and list rendering regression checks."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[1] / "src/plugin.video.streamhub/resources/streamhub/resume.py"


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.enabled = True
        self.home = Mock()
        utils = types.SimpleNamespace(translatePath=lambda path: self.directory.name,
            log=Mock(), home=self.home, format_exc=lambda: "error",
            getSetting=lambda key: "true" if self.enabled else "false")
        spec = importlib.util.spec_from_file_location("tested_resume", SOURCE)
        self.resume = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"streamhub.utils": utils}):
            spec.loader.exec_module(self.resume)

    def test_season_zero_has_its_own_episode_identity_for_int_and_string_values(self):
        for season in (0, "0"):
            self.assertEqual(self.resume.key_for({"tmdb_id": 123, "season": season, "episode": 1}), "t123:s0e1")
        self.assertEqual(self.resume.key_for({"tmdb_id": 123, "_s": 0, "_e": 1}), "t123:s0e1")
        self.assertEqual(self.resume.key_for({"tmdb_id": 123}), "t123")

    def test_disabled_resume_does_not_announce_playback(self):
        self.enabled = False
        self.resume.announce_playing({"tmdb_id": 123})
        self.home.setProperty.assert_not_called()
        self.home.clearProperty.assert_called_once_with("streamhub_playing")

    def test_rendering_resume_reads_store_once(self):
        entry = {"pos": 100, "total": 1000, "watched": False}
        self.resume._load = Mock(return_value={"t123": entry})
        item = Mock()
        item.getLabel.return_value = "Film"
        self.resume.decorate(item, {"tmdb_id": 123})
        self.resume._load.assert_called_once()
        self.resume._load.reset_mock()
        self.resume.apply_resume(item, {"tmdb_id": 123})
        self.resume._load.assert_called_once()
        item.setProperty.assert_any_call("ResumeTime", "100")

    def test_announcement_pairs_resume_with_media_without_auth_headers(self):
        self.resume.announce_playing({"tmdb_id": 123, "title": "Film"},
                                    expected_url="https://cdn.invalid/movie.mp4|Cookie=secret")
        payload = json.loads(self.home.setProperty.call_args.args[1])
        self.assertEqual(payload["key"], "t123")
        self.assertEqual(payload["expected_url"], "https://cdn.invalid/movie.mp4")

    def test_invalid_saved_position_does_not_break_resume(self):
        self.resume._load = Mock(return_value={"t123": {"pos": "broken"}})
        self.assertEqual(self.resume.resume_seconds("t123"), 0)

    def test_successful_save_uses_unique_temporary_file_and_leaves_no_temp(self):
        real_replace = self.resume.os.replace
        with patch.object(self.resume.os, "replace", wraps=real_replace) as replace:
            self.resume.record("t123", 100, 1000, "Film")
            self.resume.record("t456", 200, 1000, "Other")
        paths = [call.args[0] for call in replace.call_args_list]
        self.assertEqual(len(set(paths)), 2)
        self.assertTrue(all(Path(path).name.startswith(".resume-") for path in paths))
        self.assertEqual(set(self.resume._load()), {"t123", "t456"})
        self.assertEqual([p.name for p in Path(self.directory.name).iterdir()], ["resume.json"])

    def test_failed_replace_preserves_previous_store_and_cleans_temporary_file(self):
        self.resume.record("t123", 100, 1000, "Film")
        previous = Path(self.resume._FILE).read_bytes()
        with patch.object(self.resume.os, "replace", side_effect=OSError("disk unavailable")):
            self.resume.record("t123", 200, 1000, "Film")
        self.assertEqual(Path(self.resume._FILE).read_bytes(), previous)
        self.assertEqual([p.name for p in Path(self.directory.name).iterdir()], ["resume.json"])


if __name__ == "__main__":
    unittest.main()
