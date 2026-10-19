"""Offline live/catalog/auth regressions; no Kodi or provider connections."""
import ast
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import sys
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qsl, urlencode, urlparse

SOURCE = Path(__file__).resolve().parents[1] / "src/plugin.video.streamhub/resources/streamhub"


def load(name, extra=None, functions=None):
    tree = ast.parse((SOURCE / name).read_text(encoding="utf-8"))
    if functions:
        tree.body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in functions]
    else:
        tree.body = [n for n in tree.body if not (isinstance(n, ast.ImportFrom) and
                     n.module in ("streamhub.utils", "streamhub.playback"))]
    ns = dict(json=json, re=re, time=time, md5=hashlib.md5, urlparse=urlparse,
              urlencode=urlencode, parse_qsl=parse_qsl, ThreadPoolExecutor=ThreadPoolExecutor,
              monitor=types.SimpleNamespace(abortRequested=lambda: False),
              log=Mock(), format_exc=lambda: "fixture error", getSetting=lambda key: "false",
              home=Mock(), dialog=Mock(), getAuthSignature=Mock(return_value="signature"),
              request=Mock(), request_json=Mock(), get_cache=Mock(return_value=(False, None)),
              set_cache=Mock(), filterout=lambda value: value, fail_resolved=Mock())
    ns.update(extra or {})
    exec(compile(tree, name, "exec"), ns)
    return ns


def split_url(url, headers=None):
    url, _, encoded = url.partition("|")
    values = dict(headers or {})
    values.update(parse_qsl(encoded))
    return url, urlencode(values)


class LiveResolveTests(unittest.TestCase):
    def setUp(self):
        self.ns = load("vjlive.py", {"split_stream_url": split_url}, functions={"resolve_link"})
        self.link = "https://vavoo.to/play/channel"

    def test_errors_and_empty_results_allow_next_mirror(self):
        for result in ([], None, {}, [None], [{}]):
            self.ns["request_json"].return_value = result
            self.assertEqual(self.ns["resolve_link"](self.link), (None, None))
        self.ns["request_json"].side_effect = RuntimeError("offline")
        self.assertEqual(self.ns["resolve_link"](self.link), (None, None))

    def test_missing_signature_does_not_start_resolver_requests(self):
        self.ns["getAuthSignature"].return_value = None
        self.assertEqual(self.ns["resolve_link"](self.link), (None, None))
        self.ns["request_json"].assert_not_called()

    def test_probe_and_playback_keep_response_and_inline_headers(self):
        self.ns["request_json"].return_value = [{"url": "https://cdn.invalid/live.m3u8|Token=abc",
                                                "headers": {"Referer": "https://player.invalid/"}}]
        self.ns["request"].return_value = types.SimpleNamespace(status_code=200)
        url, encoded = self.ns["resolve_link"](self.link)
        self.assertEqual(url, "https://cdn.invalid/live.m3u8")
        self.assertEqual(dict(parse_qsl(encoded)), {"Referer": "https://player.invalid/", "Token": "abc"})
        self.assertEqual(self.ns["request"].call_args.kwargs["headers"], dict(parse_qsl(encoded)))
        self.assertEqual(self.ns["request"].call_args.kwargs["max_bytes"], 0)
        self.assertNotIn("content-length", self.ns["request_json"].call_args.kwargs["headers"])

    def test_stalker_failure_always_returns_pair(self):
        portal = types.ModuleType("streamhub.stalker")
        portal.StalkerPortal = Mock(side_effect=RuntimeError("offline"))
        self.ns["get_cache_or_setting"] = lambda key: "fixture"
        with patch.dict(sys.modules, {"streamhub.stalker": portal}):
            self.assertEqual(self.ns["resolve_link"]("ffmpeg http://portal.invalid/channel"), (None, None))


class VavooCatalogTests(unittest.TestCase):
    def setUp(self):
        self.cache = {}
        self.ns = load("vavoo_tv.py", {"get_cache": lambda key: (key in self.cache, self.cache.get(key))})
        def write(key, value, timeout=False):
            self.cache[key] = value
        self.ns["set_cache"] = Mock(side_effect=write)
        self.row = {"url": "https://vavoo.to/channel", "name": "Channel", "group": "Germany"}

    def test_channel_switch_reuses_cached_index(self):
        response = Mock(text=json.dumps([self.row, self.row]))
        self.ns["request"].return_value = response
        first = self.ns["vavoo_groups"]()
        self.assertEqual(self.ns["vavoo_groups"](), first)
        self.assertEqual(first[0], ["Germany"])
        self.ns["request"].assert_called_once()
        self.assertEqual(self.ns["set_cache"].call_args.kwargs["timeout"], 10.0 / 60)

    def test_index_failure_uses_previous_channels(self):
        self.cache["groups"] = ["Germany"]
        self.cache["vav_channels"] = {"key": "old", "channels": [self.row]}
        self.ns["vavoo_groups"] = Mock(side_effect=RuntimeError("offline"))
        self.assertEqual(self.ns["get_vav_channels"](), {"Channel": [self.row["url"]]})
        self.ns["getAuthSignature"].assert_not_called()

    def test_repeated_cursor_stops_and_deduplicates_rows(self):
        self.ns["request_json"].return_value = {"items": [self.row, self.row, None], "nextCursor": 0}
        group, rows, complete = self.ns["new_vav_channels"]("Germany", "signature")
        self.assertFalse(complete)
        self.assertEqual(rows, [self.row])
        self.ns["request_json"].assert_called_once()

    def test_valid_pagination_keeps_all_pages(self):
        second = dict(self.row, url="https://vavoo.to/two")
        self.ns["request_json"].side_effect = [{"items": [self.row], "nextCursor": "two"},
                                             {"items": [second], "nextCursor": None}]
        self.assertEqual(self.ns["new_vav_channels"]("Germany", "signature"),
                         ("Germany", [self.row, second], True))
        self.assertEqual(self.ns["request_json"].call_count, 2)

    def test_catalog_time_budget_and_shutdown_stop_requests(self):
        self.ns["time"] = types.SimpleNamespace(monotonic=Mock(side_effect=[0, 21]))
        self.assertEqual(self.ns["new_vav_channels"]("Germany", "signature"), ("Germany", [], False))
        self.ns["request_json"].assert_not_called()
        self.ns["time"] = time
        self.ns["monitor"].abortRequested = lambda: True
        self.assertFalse(self.ns["new_vav_channels"]("Germany", "signature")[2])
        self.ns["request_json"].assert_not_called()

    def test_missing_signature_does_not_repeat_auth_in_group_workers(self):
        self.cache["groups"] = ["Germany", "Austria"]
        self.ns["vavoo_groups"] = Mock(return_value=(["Germany", "Austria"], "hash"))
        self.ns["getAuthSignature"].return_value = None
        self.assertEqual(self.ns["get_vav_channels"](), {})
        self.ns["getAuthSignature"].assert_called_once()
        self.ns["request_json"].assert_not_called()


class AuthRefreshTests(unittest.TestCase):
    def setUp(self):
        self.cache = {}
        self.ns = load("utils.py", {"_AUTH_LOCK": threading.Lock(),
            "memcache_get": lambda bucket, key, default=None: self.cache.get(key, default),
            "memcache_set": lambda bucket, key, value, **kw: self.cache.__setitem__(key, value)},
            functions={"getAuthSignature", "_fetch_auth_signature"})

    def test_concurrent_refresh_makes_one_request(self):
        entered, release = threading.Event(), threading.Event()
        def response(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(2))
            return {"addonSig": "valid"}
        self.ns["request_json"].side_effect = response
        with ThreadPoolExecutor(max_workers=4) as pool:
            first = pool.submit(self.ns["getAuthSignature"])
            self.assertTrue(entered.wait(2))
            others = [pool.submit(self.ns["getAuthSignature"]) for _ in range(3)]
            release.set()
            self.assertEqual([first.result()] + [f.result() for f in others], ["valid"] * 4)
        self.ns["request_json"].assert_called_once()

    def test_auth_failure_is_bounded_and_not_repeated_by_other_sources(self):
        self.ns["request_json"].side_effect = RuntimeError("offline")
        self.assertIsNone(self.ns["getAuthSignature"]())
        self.assertIsNone(self.ns["getAuthSignature"]())
        self.assertEqual(self.ns["request_json"].call_count, 2)
        self.assertEqual(self.ns["request_json"].call_args.kwargs["retries"], 0)
        self.assertLessEqual(self.ns["request_json"].call_args.kwargs["timeout"], 6)
        self.cache.pop("failed")
        self.ns["request_json"].side_effect = None
        self.ns["request_json"].return_value = {"addonSig": "recovered"}
        self.assertEqual(self.ns["getAuthSignature"](), "recovered")


class SportProtocolTests(unittest.TestCase):
    def test_display_label_does_not_change_livetv_player_type(self):
        original = {"type": "embed", "c": "id", "lang": "DE"}
        live = types.ModuleType("streamhub.livetv")
        live.fetch_links = Mock(return_value=[original])
        live.resolve_link = Mock(return_value=("https://cdn.invalid/live.m3u8", None))
        movies = types.ModuleType("streamhub.movies")
        movies.pref_lang = lambda: "DE"
        progress = Mock()
        progress.iscanceled.return_value = False
        ns = load("sport.py", {"StatusProgress": Mock(return_value=progress), "ListItem": Mock(),
                  "ListItemInfoTag": Mock(), "configure_playback": lambda item, url, headers, **kw: url,
                  "play_directly": Mock()})
        with patch.dict(sys.modules, {"streamhub.livetv": live, "streamhub.movies": movies}):
            ns["play"]({"event": json.dumps({"url": "https://livetv.invalid/event", "title": "Match"})})
        self.assertEqual(live.resolve_link.call_args.args[0]["type"], "embed")
        self.assertEqual(original, {"type": "embed", "c": "id", "lang": "DE"})
        ns["play_directly"].assert_called_once()


class ManualSelectionCancellationTests(unittest.TestCase):
    @staticmethod
    def progress(cancelled=False):
        progress = Mock()
        progress.iscanceled.return_value = cancelled
        progress.close.side_effect = lambda: setattr(progress.iscanceled, "return_value", False)
        return progress

    def test_cancel_during_sport_lookup_never_opens_picker(self):
        progress = self.progress()
        live = types.ModuleType("streamhub.livetv")
        def links(url):
            progress.iscanceled.return_value = True
            return [{"type": "embed", "c": "one"}, {"type": "embed", "c": "two"}]
        live.fetch_links = Mock(side_effect=links)
        live.resolve_link = Mock()
        movies = types.ModuleType("streamhub.movies")
        movies.pref_lang = lambda: "DE"
        ns = load("sport.py", {"StatusProgress": Mock(return_value=progress),
                  "getSetting": lambda key: "true", "selectDialog": Mock(return_value=0)})
        with patch.dict(sys.modules, {"streamhub.livetv": live, "streamhub.movies": movies}):
            ns["play"]({"event": json.dumps({"url": "https://livetv.invalid/event"})})
        ns["selectDialog"].assert_not_called()
        live.resolve_link.assert_not_called()
        ns["fail_resolved"].assert_called_once()

    def test_manual_live_selection_has_new_cancellable_progress(self):
        first, second = self.progress(), self.progress(True)
        candidates = [{"url": "https://vavoo.to/one", "label": "First"},
                      {"url": "https://vavoo.to/two", "label": "Second"}]
        hosters = types.ModuleType("streamhub.hosters")
        hosters.label_live_streams = lambda values: candidates
        hosters.filter_alive = Mock()
        hosters.live_display = lambda candidate, number: candidate["label"]
        ns = load("vjlive.py", {"StatusProgress": Mock(side_effect=[first, second]),
                  "getchannels": lambda *args: {"Channel": [c["url"] for c in candidates]},
                  "getSetting": lambda key: "true" if key == "live_host_select" else "false",
                  "_live_breakdown": lambda values: "", "selectDialog": Mock(return_value=0),
                  "resolve_link": Mock()}, functions={"livePlay"})
        with patch.dict(sys.modules, {"streamhub.hosters": hosters}):
            ns["livePlay"]("Channel")
        self.assertEqual(ns["StatusProgress"].call_count, 2)
        ns["resolve_link"].assert_not_called()
        ns["fail_resolved"].assert_called_once()

    def test_manual_sport_selection_has_new_cancellable_progress(self):
        first, second = self.progress(), self.progress(True)
        live = types.ModuleType("streamhub.livetv")
        live.fetch_links = Mock(return_value=[{"type": "embed", "c": "one"}, {"type": "embed", "c": "two"}])
        live.resolve_link = Mock()
        movies = types.ModuleType("streamhub.movies")
        movies.pref_lang = lambda: "DE"
        ns = load("sport.py", {"StatusProgress": Mock(side_effect=[first, second]),
                  "getSetting": lambda key: "true", "selectDialog": Mock(return_value=0)})
        with patch.dict(sys.modules, {"streamhub.livetv": live, "streamhub.movies": movies}):
            ns["play"]({"event": json.dumps({"url": "https://livetv.invalid/event"})})
        self.assertEqual(ns["StatusProgress"].call_count, 2)
        live.resolve_link.assert_not_called()
        ns["fail_resolved"].assert_called_once()


if __name__ == "__main__":
    unittest.main()
