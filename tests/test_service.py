"""Service scheduling and resume callbacks with a fake Kodi player and clock."""
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[1] / "src/plugin.video.streamhub/resources/service.py"


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.clock = 100.0
        self.state = {"playing": True, "pos": 0, "total": 1200,
                      "file": "https://first.example/video"}
        self.players = []
        state, players = self.state, self.players

        class Player:
            def __init__(player):
                players.append(player)
                player.seeks = []

            def isPlaying(player):
                return state["playing"]

            def getTime(player):
                return state["pos"]

            def getTotalTime(player):
                return state["total"]

            def getPlayingFile(player):
                return state["file"]

            def seekTime(player, pos):
                player.seeks.append(pos)
                state["pos"] = pos

        class Monitor:
            def abortRequested(monitor):
                return False

        self.kodi = types.ModuleType("xbmc")
        self.kodi.Player = Player
        self.kodi.Monitor = Monitor
        self.kodi.LOGINFO, self.kodi.LOGWARNING, self.kodi.LOGERROR = 1, 2, 3
        self.kodi.log, self.kodi.sleep, self.kodi.executebuiltin = Mock(), Mock(), Mock()
        self.gui = types.SimpleNamespace(Dialog=Mock())
        self.home = Mock()
        self.home.getProperty.return_value = ""
        self.home.clearProperty.side_effect = lambda key: setattr(self.home.getProperty, "return_value", "")
        self.resume = types.ModuleType("streamhub.resume")
        self.resume.record, self.resume.mark_watched = Mock(), Mock()
        package = types.ModuleType("streamhub")
        package.resume = self.resume
        self.transition = {"active": False}
        self.failover = types.ModuleType("streamhub.failover")
        self.failover.transition_active = lambda: self.transition["active"]
        self.controller = Mock()
        self.failover.FailoverPlayer = Mock(return_value=self.controller)
        self.modules = patch.dict(sys.modules, {
            "xbmc": self.kodi, "xbmcaddon": types.SimpleNamespace(Addon=Mock()),
            "xbmcgui": self.gui, "xbmcvfs": types.SimpleNamespace(translatePath=lambda x: x),
            "streamhub": package, "streamhub.resume": self.resume,
            "streamhub.utils": types.SimpleNamespace(home=self.home),
            "streamhub.failover": self.failover,
        })
        self.modules.start()
        self.addCleanup(self.modules.stop)
        spec = importlib.util.spec_from_file_location("tested_service", SOURCE)
        self.service = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.service)
        self.service.time = types.SimpleNamespace(time=lambda: self.clock, monotonic=lambda: self.clock)
        self.service._setting = Mock(return_value="false")
        self.service._installed_version = Mock(return_value="1")
        self.service._maybe_offer_restart = Mock()
        self.service._remote_md5 = Mock(return_value="digest")
        self.service._fetch_remote_version = Mock(return_value="2")
        self.service._load_state = Mock(return_value={})
        self.service._save_state = Mock()
        self.service._download_zip = Mock(return_value=b"archive")
        self.service._install_zip = Mock(return_value=(True, None))

    def tracker(self, resume=0):
        tracker = self.service.ResumeTracker()
        self.home.getProperty.return_value = json.dumps({"key": "t123", "title": "Film", "resume": resume})
        tracker.onPlayBackStarted()
        tracker.onAVStarted()
        return tracker

    def tick(self, tracker, seconds=1):
        self.clock += seconds
        tracker.sample()

    def test_resume_callback_never_sleeps_or_seeks_before_kodi_has_had_a_tick(self):
        tracker = self.tracker(300)
        tracker.sample()
        self.assertEqual(tracker.seeks, [])
        self.kodi.sleep.assert_not_called()
        self.resume.record.assert_not_called()

    def test_native_resume_does_not_trigger_a_second_seek(self):
        tracker = self.tracker(300)
        self.state["pos"] = 303
        self.tick(tracker)
        self.assertEqual(tracker.seeks, [])
        self.assertEqual(tracker._pos, 303)
        self.resume.record.assert_called_once_with("t123", 303, 1200, "Film")

    def test_fallback_seek_is_once_and_waits_for_reported_position(self):
        tracker = self.tracker(300)
        self.tick(tracker)
        self.assertEqual(tracker.seeks, [300])
        self.resume.record.assert_not_called()
        self.tick(tracker)
        self.assertEqual(tracker.seeks, [300])
        self.resume.record.assert_called_once_with("t123", 300, 1200, "Film")

    def test_user_backwards_seek_during_startup_overrides_pending_resume(self):
        tracker = self.tracker(300)
        self.state["pos"] = 10
        tracker.onPlayBackSeek(10000, -290000)
        self.tick(tracker)
        self.assertEqual(tracker.seeks, [])
        self.assertEqual(tracker._pos, 10)
        tracker.onPlayBackStopped()
        self.resume.record.assert_called_once_with("t123", 10, 1200, "Film")

    def test_internal_seek_is_marked_before_synchronous_callback(self):
        tracker = self.tracker(300)
        seek = tracker.seekTime
        observed = []
        def callback_seek(pos):
            observed.append(tracker._seek_requested)
            tracker.onPlayBackSeek(pos * 1000, pos * 1000)
            self.assertFalse(tracker._seeked)
            seek(pos)
        tracker.seekTime = callback_seek
        self.tick(tracker)
        self.tick(tracker)
        self.assertEqual(observed, [True])
        self.assertEqual(tracker.seeks, [300])
        self.assertEqual(tracker._pos, 300)

    def test_unknown_duration_retries_without_blocking_then_expires(self):
        tracker = self.tracker(300)
        self.state["total"] = 0
        self.tick(tracker, 2)
        self.assertFalse(tracker._seeked)
        self.tick(tracker, 10)
        self.assertTrue(tracker._seeked)
        self.assertEqual(tracker.seeks, [])
        self.kodi.sleep.assert_not_called()

    def test_pause_does_not_rewrite_unchanged_progress(self):
        tracker = self.tracker()
        self.state["pos"] = 120
        self.tick(tracker)
        for _ in range(6):
            self.tick(tracker, 20)
        self.resume.record.assert_called_once_with("t123", 120, 1200, "Film")
        self.state["total"] = 1300
        self.tick(tracker, 20)
        self.assertEqual(self.resume.record.call_count, 2)
        self.assertEqual(self.resume.record.call_args.args[2], 1300)

    def test_seek_to_zero_is_saved_instead_of_retaining_old_position(self):
        tracker = self.tracker()
        self.state["pos"] = 120
        self.tick(tracker)
        self.state["pos"] = 0
        self.tick(tracker, 20)
        self.assertEqual(tracker._pos, 0)
        self.resume.record.assert_called_with("t123", 0, 1200, "Film")

    def test_stop_before_valid_sample_preserves_previous_resume(self):
        tracker = self.tracker(300)
        tracker.onPlayBackStopped()
        self.resume.record.assert_not_called()
        self.assertIsNone(tracker._key)
        self.assertEqual(tracker._pending_resume, 0)

    def test_playback_error_keeps_latest_sample_and_does_not_autoplay(self):
        tracker = self.tracker()
        tracker._autoplay_next = Mock()
        self.state["pos"] = 200
        self.tick(tracker)
        tracker.onPlayBackError()
        self.resume.record.assert_called_with("t123", 200, 1200, "Film")
        tracker._autoplay_next.assert_not_called()
        self.assertIsNone(tracker._key)

    def test_natural_end_marks_unknown_duration_watched(self):
        tracker = self.tracker()
        tracker._autoplay_next = Mock()
        self.state.update(pos=40, total=0)
        self.tick(tracker)
        tracker.onPlayBackEnded()
        self.resume.mark_watched.assert_called_once_with("t123", "Film")
        tracker._autoplay_next.assert_called_once_with("t123")

    def test_handoff_samples_and_callbacks_preserve_the_last_valid_position(self):
        tracker = self.tracker()
        self.state["pos"] = 320
        self.tick(tracker)
        self.resume.record.reset_mock()
        tracker._autoplay_next = Mock()
        self.transition["active"] = True
        self.state.update(pos=0, total=20)
        self.home.getProperty.return_value = json.dumps({"key": "wrong", "resume": 0})
        self.tick(tracker, 30)
        tracker.onPlayBackStarted()
        tracker.onAVStarted()
        tracker.onPlayBackSeek(0, -320000)
        tracker.onPlayBackStopped()
        tracker.onPlayBackEnded()
        tracker.onPlayBackError()
        tracker._flush()
        self.assertEqual(tracker._key, "t123")
        self.assertEqual((tracker._pos, tracker._total), (320, 1200))
        self.resume.record.assert_not_called()
        self.resume.mark_watched.assert_not_called()
        tracker._autoplay_next.assert_not_called()
        self.assertTrue(self.home.getProperty.return_value)

    def test_managed_handoff_is_consumed_after_the_av_callback_without_seeking(self):
        tracker = self.tracker()
        self.state["pos"] = 320
        self.tick(tracker)
        self.transition["active"] = True
        tracker.onPlayBackStopped()
        tracker.onAVStarted()
        self.assertTrue(tracker._transition_seen)
        self.state.update(pos=321, total=1202, file="https://second.example/video")
        self.home.getProperty.return_value = json.dumps({
            "key": "t123", "title": "Film", "resume": 320, "managed_resume": True})
        self.transition["active"] = False
        self.resume.record.reset_mock()
        self.tick(tracker)
        self.assertEqual(tracker._key, "t123")
        self.assertTrue(tracker._av_started)
        self.assertTrue(tracker._seeked)
        self.assertFalse(tracker._transition_seen)
        self.assertEqual(tracker.seeks, [])
        self.assertEqual(self.home.getProperty.return_value, "")
        self.resume.record.assert_called_once_with("t123", 321, 1202, "Film")
        tracker.onAVStarted()
        self.tick(tracker)
        self.assertEqual(tracker._key, "t123")
        self.assertEqual(tracker.seeks, [])

    def test_managed_payload_can_start_an_idle_tracker_without_player_callbacks(self):
        tracker = self.service.ResumeTracker()
        self.state["pos"] = 470
        self.home.getProperty.return_value = json.dumps({
            "key": "t456", "title": "Replacement", "resume": 470, "managed_resume": True})
        self.tick(tracker)
        self.resume.record.assert_called_once_with("t456", 470, 1200, "Replacement")
        self.assertTrue(tracker._av_started)
        self.assertTrue(tracker._seeked)
        self.assertEqual(tracker.seeks, [])

    def test_managed_payload_arriving_with_av_callback_also_avoids_second_seek(self):
        tracker = self.service.ResumeTracker()
        self.state["pos"] = 299
        self.home.getProperty.return_value = json.dumps({
            "key": "t456", "title": "Replacement", "resume": 300, "managed_resume": True})
        tracker.onAVStarted()
        self.tick(tracker)
        self.assertEqual(tracker.seeks, [])
        self.resume.record.assert_called_once_with("t456", 299, 1200, "Replacement")

    def test_normal_announcement_waits_for_start_callbacks(self):
        tracker = self.service.ResumeTracker()
        self.home.getProperty.return_value = json.dumps({"key": "t123", "resume": 300})
        self.state["pos"] = 300
        self.tick(tracker)
        self.assertIsNone(tracker._key)
        self.assertTrue(self.home.getProperty.return_value)
        self.resume.record.assert_not_called()
        tracker.onPlayBackStarted()
        tracker.onAVStarted()
        self.tick(tracker)
        self.resume.record.assert_called_once_with("t123", 300, 1200, "")

    def test_canceled_handoff_commits_only_the_old_valid_sample_then_releases_key(self):
        tracker = self.tracker()
        self.state["pos"] = 400
        self.tick(tracker)
        self.transition["active"] = True
        tracker.onPlayBackStopped()
        self.state.update(playing=False, pos=0)
        self.transition["active"] = False
        self.resume.record.reset_mock()
        tracker._autoplay_next = Mock()
        self.tick(tracker)
        self.resume.record.assert_called_once_with("t123", 400, 1200, "Film")
        self.assertIsNone(tracker._key)
        self.resume.mark_watched.assert_not_called()
        tracker._autoplay_next.assert_not_called()

    def test_unrelated_playback_after_canceled_handoff_does_not_inherit_resume_key(self):
        tracker = self.tracker()
        self.state["pos"] = 400
        self.tick(tracker)
        self.transition["active"] = True
        tracker.onAVStarted()
        self.transition["active"] = False
        self.state.update(pos=0, file="https://unrelated.example/film")
        self.tick(tracker)
        self.assertIsNone(tracker._key)
        self.resume.record.reset_mock()
        self.state["pos"] = 500
        tracker.onAVStarted()
        self.tick(tracker, 20)
        self.resume.record.assert_not_called()

    def test_unannounced_file_change_clears_old_playback_identity(self):
        tracker = self.tracker()
        self.state["pos"] = 400
        self.tick(tracker)
        self.resume.record.reset_mock()
        self.state.update(pos=800, file="https://another.example/movie")
        tracker.onAVStarted()
        self.tick(tracker, 20)
        self.assertIsNone(tracker._key)
        self.resume.record.assert_not_called()

    def test_transition_import_error_preserves_normal_resume_behavior(self):
        with patch.dict(sys.modules, {"streamhub.failover": None}):
            tracker = self.tracker()
            self.state["pos"] = 120
            self.tick(tracker)
            self.resume.record.assert_called_once_with("t123", 120, 1200, "Film")

    def test_new_title_started_during_handoff_keeps_its_deferred_av_readiness(self):
        tracker = self.tracker()
        self.state["pos"] = 400
        self.tick(tracker)
        self.resume.record.reset_mock()
        self.transition["active"] = True
        tracker.onPlayBackStopped()
        self.state.update(pos=0, total=1800, file="https://new.example/movie|User-Agent=Kodi")
        self.home.getProperty.return_value = json.dumps({
            "key": "t456", "title": "New film", "resume": 600,
            "expected_url": "https://new.example/movie"})
        tracker.onPlayBackStarted()
        tracker.onAVStarted()
        self.transition["active"] = False
        self.tick(tracker)
        self.assertEqual(tracker._key, "t456")
        self.assertTrue(tracker._av_started)
        self.assertFalse(tracker._seeked)
        self.assertEqual(self.home.getProperty.return_value, "")
        self.resume.record.assert_called_once_with("t123", 400, 1200, "Film")
        self.tick(tracker)
        self.tick(tracker)
        self.assertEqual(tracker.seeks, [600])
        self.resume.record.assert_called_with("t456", 600, 1800, "New film")

    def test_deferred_av_file_cannot_adopt_an_announcement_for_a_different_file(self):
        tracker = self.tracker()
        self.state["pos"] = 400
        self.tick(tracker)
        self.resume.record.reset_mock()
        self.transition["active"] = True
        self.state.update(pos=0, file="https://candidate.example/movie")
        tracker.onAVStarted()
        self.home.getProperty.return_value = json.dumps({
            "key": "t456", "title": "Other film", "resume": 600,
            "expected_url": "https://different.example/movie"})
        self.transition["active"] = False
        self.tick(tracker)
        self.assertIsNone(tracker._key)
        self.assertEqual(tracker.seeks, [])
        self.assertTrue(self.home.getProperty.return_value)
        self.resume.record.assert_called_once_with("t123", 400, 1200, "Film")

    def test_deferred_av_readiness_cannot_transfer_to_a_later_unobserved_file(self):
        tracker = self.tracker()
        self.transition["active"] = True
        self.state["file"] = "https://first-candidate.example/movie"
        tracker.onAVStarted()
        self.state["file"] = "https://later.example/movie"
        self.home.getProperty.return_value = json.dumps({
            "key": "t456", "resume": 600, "expected_url": self.state["file"]})
        self.transition["active"] = False
        self.tick(tracker)
        self.assertIsNone(tracker._key)
        self.assertTrue(self.home.getProperty.return_value)
        self.assertEqual(tracker.seeks, [])

    def test_expected_url_prevents_managed_resume_from_attaching_to_another_movie(self):
        tracker = self.service.ResumeTracker()
        self.state["pos"] = 600
        self.home.getProperty.return_value = json.dumps({
            "key": "t456", "resume": 600, "managed_resume": True,
            "expected_url": "https://different.example/movie"})
        self.tick(tracker)
        self.assertIsNone(tracker._key)
        self.resume.record.assert_not_called()

    def test_expected_url_accepts_kodi_inline_headers(self):
        tracker = self.service.ResumeTracker()
        self.state.update(pos=600, file="https://new.example/movie|User-Agent=Kodi")
        self.home.getProperty.return_value = json.dumps({
            "key": "t456", "resume": 600, "managed_resume": True,
            "expected_url": "https://new.example/movie"})
        self.tick(tracker)
        self.assertEqual(tracker._key, "t456")
        self.resume.record.assert_called_once_with("t456", 600, 1200, "")

    def test_automatic_update_waits_during_handoff_without_an_active_decoder(self):
        self.state["playing"] = False
        self.transition["active"] = True
        self.assertTrue(self.service._playback_active())
        self.assertIsNone(self.service.check_for_update())
        self.service._remote_md5.assert_not_called()
        self.service._download_zip.assert_not_called()

    def test_buffer_maintenance_waits_during_handoff_without_an_active_decoder(self):
        self.state["playing"] = False
        self.transition["active"] = True
        self.service._add_resources_path = Mock()
        self.service._last_check_ts = Mock(return_value=0)
        self.service.STARTUP_DELAY = 0
        self.service._apply_buffer = Mock()
        self.service.check_for_update = Mock()
        self.service.SettingsMonitor = Mock(return_value=types.SimpleNamespace(
            abortRequested=lambda: False, waitForAbort=lambda _: True, buffer_changed=False))
        self.service.main()
        self.service._apply_buffer.assert_not_called()
        self.service.check_for_update.assert_not_called()

    def test_service_samples_controller_first_and_shutdown_runs_after_final_flush(self):
        calls = []
        self.service._add_resources_path = Mock(side_effect=lambda: calls.append("path"))
        self.service._last_check_ts = Mock(return_value=9999999)
        self.service._apply_buffer = Mock()
        tracker = Mock()
        self.service.ResumeTracker = Mock(side_effect=lambda: calls.append("tracker") or tracker)
        self.failover.FailoverPlayer.side_effect = lambda: calls.append("controller") or self.controller
        self.controller.sample.side_effect = lambda: calls.append("control_sample")
        tracker.sample.side_effect = lambda: calls.append("resume_sample")
        tracker._flush.side_effect = lambda: calls.append("flush")
        self.controller.shutdown.side_effect = lambda: calls.append("shutdown")
        self.service.SettingsMonitor = Mock(return_value=types.SimpleNamespace(
            abortRequested=lambda: False, waitForAbort=lambda _: True, buffer_changed=False))
        self.service.main()
        self.assertEqual(calls, ["path", "controller", "tracker", "control_sample", "resume_sample", "flush", "shutdown"])

    def test_service_shutdown_still_runs_when_loop_fails(self):
        self.service._add_resources_path = Mock()
        self.service._last_check_ts = Mock(return_value=9999999)
        tracker = Mock()
        tracker.sample.side_effect = RuntimeError("unexpected test failure")
        self.service.ResumeTracker = Mock(return_value=tracker)
        self.service.SettingsMonitor = Mock(return_value=types.SimpleNamespace(
            abortRequested=lambda: False, waitForAbort=lambda _: True, buffer_changed=False))
        with self.assertRaises(RuntimeError):
            self.service.main()
        tracker._flush.assert_called_once()
        self.controller.shutdown.assert_called_once()

    def test_automatic_update_does_no_network_work_during_playback(self):
        self.assertIsNone(self.service.check_for_update())
        self.service._remote_md5.assert_not_called()
        self.service._fetch_remote_version.assert_not_called()
        self.service._install_zip.assert_not_called()

    def test_playback_beginning_during_checksum_stops_further_metadata_requests(self):
        self.state["playing"] = False
        def checksum():
            self.state["playing"] = True
            return "digest"
        self.service._remote_md5.side_effect = checksum
        self.assertIsNone(self.service.check_for_update())
        self.service._fetch_remote_version.assert_not_called()
        self.service._download_zip.assert_not_called()

    def test_automatic_check_reuses_initial_checksum(self):
        self.state["playing"] = False
        self.assertTrue(self.service.check_for_update())
        self.service._remote_md5.assert_called_once()
        self.assertEqual(self.service._save_state.call_args.args[0]["md5"], "digest")

    def test_playback_beginning_during_manifest_defers_download(self):
        self.state["playing"] = False
        def manifest():
            self.state["playing"] = True
            return "2"
        self.service._fetch_remote_version.side_effect = manifest
        self.assertIsNone(self.service.check_for_update())
        self.service._download_zip.assert_not_called()
        self.service._remote_md5.assert_called_once()
        self.service._save_state.assert_not_called()

    def test_playback_beginning_during_download_defers_install(self):
        self.state["playing"] = False
        def download(version):
            self.state["playing"] = True
            return b"archive"
        self.service._download_zip.side_effect = download
        self.assertIsNone(self.service.check_for_update())
        self.service._install_zip.assert_not_called()

    def test_manual_update_retains_explicit_behavior_during_playback(self):
        self.assertTrue(self.service.check_for_update(force=True))
        self.service._download_zip.assert_called_once_with("2")
        self.service._install_zip.assert_called_once_with(b"archive", "2")

    def test_tracker_is_active_during_startup_delay_and_maintenance_waits(self):
        self.service._add_resources_path = Mock()
        self.service._apply_buffer = Mock()
        self.service._last_check_ts = Mock(return_value=0)
        self.service._set_last_check_ts = Mock()
        self.service.check_for_update = Mock(return_value=False)
        self.service.CHECK_INTERVAL_HOURS = 0
        waits = []
        test = self
        class LoopMonitor:
            buffer_changed = False
            def abortRequested(self):
                return False
            def waitForAbort(self, seconds):
                waits.append(seconds)
                tracker = next(p for p in test.players if isinstance(p, test.service.ResumeTracker))
                if len(waits) == 1:
                    test.home.getProperty.return_value = json.dumps({"key": "t123", "resume": 0})
                    tracker.onPlayBackStarted()
                    tracker.onAVStarted()
                test.clock += seconds
                test.state["pos"] += seconds
                if len(waits) <= 31:
                    test.service.check_for_update.assert_not_called()
                    test.service._apply_buffer.assert_not_called()
                if len(waits) == 32:
                    test.state["playing"] = False
                return len(waits) >= 33
        self.service.SettingsMonitor = LoopMonitor
        self.service.main()
        self.assertTrue(all(t == 1 for t in waits))
        self.service._apply_buffer.assert_called_once()
        self.service.check_for_update.assert_called_once()
        self.service._last_check_ts.assert_called_once()
        self.service._set_last_check_ts.assert_called_once()
        self.assertGreaterEqual(self.resume.record.call_count, 1)


if __name__ == "__main__":
    unittest.main()
