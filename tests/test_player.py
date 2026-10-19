"""Legacy player completion handling without a Kodi runtime."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[1] / "src/plugin.video.streamhub/resources/streamhub/player.py"


class PlayerTests(unittest.TestCase):
    def setUp(self):
        self.monitor = Mock()
        self.monitor.abortRequested.return_value = False
        self.utils = types.ModuleType("streamhub.utils")
        self.utils.player = object
        self.utils.monitor = self.monitor
        spec = importlib.util.spec_from_file_location("tested_player", SOURCE)
        self.player = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"streamhub.utils": self.utils}):
            spec.loader.exec_module(self.player)

    def test_error_callback_ends_waiting_and_reports_failure(self):
        player = self.player.XstreamPlayer()
        player.onPlayBackError()
        self.assertTrue(player.streamFinished)
        self.assertFalse(player.streamSuccess)

    def test_stop_between_playing_check_and_time_read_does_not_crash_waiter(self):
        player = self.player.XstreamPlayer()
        player.isPlayingVideo = Mock(return_value=True)
        player.getTime = Mock(side_effect=RuntimeError("not playing"))
        self.monitor.waitForAbort.side_effect = lambda seconds: player.onPlayBackError()
        self.player.XstreamPlayer = Mock(return_value=player)
        self.assertFalse(self.player.cPlayer().startPlayer())
        self.monitor.waitForAbort.assert_called_once_with(1)


if __name__ == "__main__":
    unittest.main()
