"""Offline regression tests; no Kodi installation or provider access required."""

import importlib.util
import json
from pathlib import Path
import types
import unittest
from unittest.mock import patch
from urllib.parse import parse_qsl


SOURCE = (Path(__file__).resolve().parents[1] / "src" / "plugin.video.streamhub"
          / "resources" / "streamhub" / "playback.py")


class ListItem:
    def __init__(self):
        self.properties = {}
        self.mime = None
        self.lookup = None
        self.path = None

    def setProperty(self, key, value):
        self.properties[key] = value

    def setMimeType(self, value):
        self.mime = value

    def setContentLookup(self, value):
        self.lookup = value

    def setPath(self, value):
        self.path = value


class PlaybackTests(unittest.TestCase):
    def setUp(self):
        self.settings = {"stream_quali": "1", "hlsinputstream": "2"}
        self.enabled = {"inputstream.adaptive", "inputstream.ffmpegdirect"}
        self.kodi = types.ModuleType("xbmc")
        self.kodi.getCondVisibility = lambda condition: (
            condition.startswith("System.AddonIsEnabled(")
            and condition[len("System.AddonIsEnabled("):-1] in self.enabled)
        self.kodi.getInfoLabel = lambda label: "21.3 (21.3.0)"
        utils = types.ModuleType("streamhub.utils")
        utils.getSetting = lambda key: self.settings.get(key, "")
        spec = importlib.util.spec_from_file_location("tested_playback", SOURCE)
        self.playback = importlib.util.module_from_spec(spec)
        with patch.dict("sys.modules", {"xbmc": self.kodi, "streamhub.utils": utils}):
            spec.loader.exec_module(self.playback)

    def configure(self, url, **kwargs):
        item = ListItem()
        result = self.playback.configure_playback(item, url, **kwargs)
        self.assertEqual(item.path, result)
        return item, result

    def test_inline_headers_win_case_insensitively_and_values_round_trip(self):
        url, encoded = self.playback.split_stream_url(
            "https://cdn.test/master.m3u8?sig=a%2Bb|referer=https%3A%2F%2Fhost.test%2F%3Fa%3D1%26b%3D2"
            "&Cookie=token%3Da%2Bb%2520&Empty=",
            {"Referer": "https://wrong.test/", "User-Agent": "Agent + 1", "Cookie": "wrong"})
        headers = dict(parse_qsl(encoded, keep_blank_values=True))
        self.assertEqual(url, "https://cdn.test/master.m3u8?sig=a%2Bb")
        self.assertEqual(headers, {
            "referer": "https://host.test/?a=1&b=2", "Cookie": "token=a+b%20",
            "User-Agent": "Agent + 1", "Empty": ""})

    def test_encoded_default_headers_are_not_double_encoded(self):
        _, encoded = self.playback.split_stream_url(
            "https://cdn.test/stream", "User-Agent=Agent%2B1&Referer=https%3A%2F%2Fsite.test%2F")
        self.assertEqual(dict(parse_qsl(encoded)), {
            "User-Agent": "Agent+1", "Referer": "https://site.test/"})

    def test_hls_sets_manifest_and_segment_headers_without_pipe(self):
        item, result = self.configure(
            "https://cdn.test/MASTER.M3U8?sig=x|Cookie=a%3Db&Referer=https%3A%2F%2Fsite.test%2F")
        self.assertEqual(result, "https://cdn.test/MASTER.M3U8?sig=x")
        self.assertEqual(item.properties["inputstream"], "inputstream.adaptive")
        self.assertEqual(item.mime, "application/vnd.apple.mpegurl")
        self.assertIs(item.lookup, False)
        self.assertEqual(item.properties["inputstream.adaptive.manifest_headers"],
                         item.properties["inputstream.adaptive.stream_headers"])
        self.assertEqual(item.properties["inputstream.adaptive.stream_selection_type"], "adaptive")
        self.assertNotIn("inputstream.adaptive.manifest_type", item.properties)
        self.assertNotIn("inputstream.adaptive.common_headers", item.properties)
        self.assertNotIn("inputstream.adaptive.config", item.properties)

    def test_quality_limits_include_safe_default_and_4k_opt_in(self):
        for setting, height, resolution in [
            ("0", 2160, "4K"), ("1", 1080, "1080p"), ("2", 720, "720p"),
            ("", 1080, "1080p"), ("invalid", 1080, "1080p")]:
            with self.subTest(setting=setting):
                self.settings["stream_quali"] = setting
                item, _ = self.configure("https://cdn.test/master.m3u8")
                self.assertEqual(self.playback.max_height(), height)
                self.assertEqual(item.properties["inputstream.adaptive.chooser_resolution_max"], resolution)

    def test_dash_uses_adaptive_and_dash_mime_even_with_hls_override(self):
        self.settings["hlsinputstream"] = "0"
        item, _ = self.configure("https://cdn.test/live.MPD?token=secret", live=True)
        self.assertEqual(item.properties["inputstream"], "inputstream.adaptive")
        self.assertEqual(item.mime, "application/dash+xml")

    def test_hls_extensionless_resolver_path(self):
        item, _ = self.configure("https://cdn.test/hls/token", live=True)
        self.assertEqual(item.properties["inputstream"], "inputstream.adaptive")

    def test_hls_directory_does_not_override_known_media_extension(self):
        item, _ = self.configure("https://cdn.test/hls/segment.ts", live=True)
        self.assertEqual(item.mime, "video/mp2t")
        self.assertEqual(item.properties["inputstream"], "inputstream.ffmpegdirect")
        self.assertNotIn("inputstream.ffmpegdirect.manifest_type", item.properties)

    def test_host_query_or_header_cannot_misclassify_mp4_as_hls(self):
        item, result = self.configure(
            "https://hls.test/video.mp4?next=playlist.m3u8|Referer=https%3A%2F%2Fsite.test%2Fhls%2F")
        self.assertNotIn("inputstream", item.properties)
        self.assertEqual(item.mime, "video/mp4")
        self.assertIn("|Referer=", result)

    def test_unknown_format_keeps_content_lookup(self):
        item, _ = self.configure("https://cdn.test/play?id=playlist.m3u8")
        self.assertIsNone(item.mime)
        self.assertIsNone(item.lookup)
        self.assertNotIn("inputstream", item.properties)

    def test_disabled_adaptive_falls_back_without_losing_headers(self):
        self.enabled.remove("inputstream.adaptive")
        item, result = self.configure("https://cdn.test/master.m3u8|Cookie=a%3Db")
        self.assertNotIn("inputstream", item.properties)
        self.assertEqual(result, "https://cdn.test/master.m3u8|Cookie=a%3Db")

    def test_explicit_live_ffmpeg_has_no_timeshift_or_unsupported_properties(self):
        self.settings["hlsinputstream"] = "0"
        item, result = self.configure("https://cdn.test/master.m3u8|User-Agent=Agent", live=True)
        self.assertEqual(item.properties, {
            "inputstream": "inputstream.ffmpegdirect",
            "inputstream.ffmpegdirect.is_realtime_stream": "true",
            "inputstream.ffmpegdirect.open_mode": "ffmpeg",
            "inputstream.ffmpegdirect.manifest_type": "hls"})
        self.assertEqual(result, "https://cdn.test/master.m3u8|User-Agent=Agent")

    def test_missing_ffmpeg_falls_back_native(self):
        self.enabled.remove("inputstream.ffmpegdirect")
        self.settings["hlsinputstream"] = "0"
        item, result = self.configure("https://cdn.test/live.m3u8|Cookie=a%3Db", live=True)
        self.assertNotIn("inputstream", item.properties)
        self.assertEqual(result, "https://cdn.test/live.m3u8|Cookie=a%3Db")

    def test_live_adaptive_setting_and_auto_both_use_adaptive(self):
        for setting in ("1", "2", ""):
            with self.subTest(setting=setting):
                self.settings["hlsinputstream"] = setting
                item, _ = self.configure("https://cdn.test/live.m3u8", live=True)
                self.assertEqual(item.properties["inputstream"], "inputstream.adaptive")

    def test_json_rpc_fallback_requires_enabled_true(self):
        def no_visibility_api(condition):
            raise RuntimeError("API unavailable")
        self.kodi.getCondVisibility = no_visibility_api
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                def rpc(request):
                    self.assertEqual(json.loads(request)["method"], "Addons.GetAddonDetails")
                    return json.dumps({"result": {"addon": {"enabled": enabled}}})
                self.kodi.executeJSONRPC = rpc
                item, _ = self.configure("https://cdn.test/master.m3u8")
                self.assertEqual("inputstream" in item.properties, enabled)

    def test_unavailable_addon_checks_fail_to_native(self):
        def unavailable(*args):
            raise RuntimeError("API unavailable")
        self.kodi.getCondVisibility = unavailable
        self.kodi.executeJSONRPC = unavailable
        item, result = self.configure("https://cdn.test/master.m3u8|Cookie=a%3Db")
        self.assertNotIn("inputstream", item.properties)
        self.assertIn("|Cookie=", result)

    def test_old_kodi_gets_required_manifest_type(self):
        self.kodi.getInfoLabel = lambda label: "20.5 (20.5.0)"
        item, _ = self.configure("https://cdn.test/master.m3u8")
        self.assertEqual(item.properties["inputstream.adaptive.manifest_type"], "hls")

    def test_kodi_22_does_not_receive_removed_manifest_type(self):
        self.kodi.getInfoLabel = lambda label: "22.0 (22.0.0)"
        item, _ = self.configure("https://cdn.test/master.m3u8")
        self.assertNotIn("inputstream.adaptive.manifest_type", item.properties)


if __name__ == "__main__":
    unittest.main()
