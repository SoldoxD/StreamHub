"""Offline regressions for status probes and bounded response reads."""
import ast
import gzip
import importlib.util
import json
from pathlib import Path
import re
import socket
import types
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.parse import urlparse

try:
    import requests as real_requests
except ImportError:
    real_requests = None

SOURCE = Path(__file__).resolve().parents[1] / "src/plugin.video.streamhub/resources/streamhub"
spec = importlib.util.spec_from_file_location("streamhub_http_test", SOURCE / "_http.py")
http = importlib.util.module_from_spec(spec)
spec.loader.exec_module(http)


def functions_from(name, names, namespace):
    """Load pure functions without importing the Kodi UI runtime."""
    tree = ast.parse((SOURCE / name).read_text(encoding="utf-8"))
    tree.body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    exec(compile(tree, str(SOURCE / name), "exec"), namespace)
    return types.SimpleNamespace(**namespace)


class RawResponse:
    def __init__(self, body=b"video data", code=200, headers=None, error=None):
        self.body = body
        self.code = self.status = code
        self.headers = headers or {}
        self.error = error
        self.reads = []
        self.closed = False

    def geturl(self):
        return "https://media.invalid/video"

    def read(self, amount=None, **kwargs):
        self.reads.append(amount)
        if self.error:
            raise self.error
        return self.body if amount is None else self.body[:amount]

    def close(self):
        self.closed = True


class VendoredHttpTests(unittest.TestCase):
    def session(self, raw, http_error=False):
        session = http.Session()
        opener = Mock()
        if http_error:
            opener.open.side_effect = HTTPError(raw.geturl(), raw.code, "error", raw.headers, raw)
        else:
            opener.open.return_value = raw
        session._opener_verify = opener
        return session

    def test_streaming_probe_does_not_download_body(self):
        raw = RawResponse()
        response = self.session(raw).get(raw.geturl(), stream=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(raw.reads, [])
        response.close()
        response.close()
        self.assertTrue(raw.closed)
        self.assertEqual(raw.reads, [])

    def test_streamed_text_is_lazy_decoded_once_and_closed(self):
        raw = RawResponse(gzip.compress(b'{"ok": true}'), headers={"Content-Encoding": "gzip"})
        response = self.session(raw).get(raw.geturl(), stream=True)
        self.assertEqual(raw.reads, [])
        self.assertEqual(response.json(), {"ok": True})
        self.assertEqual(response.content, b'{"ok": true}')
        self.assertEqual(raw.reads, [None])
        self.assertTrue(raw.closed)

    def test_nonstream_response_still_downloads_and_closes(self):
        raw = RawResponse(b"page")
        response = self.session(raw).get(raw.geturl())
        self.assertEqual(response.content, b"page")
        self.assertEqual(raw.reads, [None])
        self.assertTrue(raw.closed)

    def test_probe_bounds_success_and_error_bodies(self):
        for code in (200, 403):
            with self.subTest(status=code):
                raw = RawResponse(b"x" * 100000, code=code)
                session = self.session(raw, http_error=code >= 400)
                response = session.get(raw.geturl(), max_bytes=32)
                self.assertEqual(response.status_code, code)
                self.assertEqual(response.content, b"x" * 32)
                self.assertEqual(raw.reads, [32])
                self.assertTrue(raw.closed)
                req = session._opener_verify.open.call_args.args[0]
                self.assertEqual(req.get_header("Accept-encoding"), "identity")

    def test_zero_byte_probe_closes_without_reading_data(self):
        raw = RawResponse()
        response = self.session(raw).get(raw.geturl(), max_bytes=0)
        self.assertEqual(response.content, b"")
        self.assertEqual(raw.reads, [0])
        self.assertTrue(raw.closed)

    def test_streamed_http_error_does_not_download_body(self):
        raw = RawResponse(code=403)
        response = self.session(raw, http_error=True).get(raw.geturl(), stream=True)
        self.assertEqual(raw.reads, [])
        with self.assertRaises(http.HTTPException):
            response.raise_for_status()
        response.close()
        self.assertTrue(raw.closed)

    def test_read_timeouts_are_retryable_and_close_socket(self):
        for cap in (None, 16):
            with self.subTest(cap=cap):
                raw = RawResponse(error=socket.timeout("timed out"))
                with self.assertRaises(http.Timeout):
                    self.session(raw).get(raw.geturl(), max_bytes=cap)
                self.assertTrue(raw.closed)

    def test_context_manager_closes_unconsumed_stream(self):
        raw = RawResponse()
        with self.session(raw).get(raw.geturl(), stream=True) as response:
            self.assertEqual(response.status_code, 200)
        self.assertTrue(raw.closed)
        self.assertEqual(raw.reads, [])


class RequestsCapTests(unittest.TestCase):
    def namespace(self, session=None, vendored=False):
        return functions_from("utils.py", {"_capped", "request"}, {
            "requests": http, "session": session,
            "_VENDORED_HTTP": vendored, "monitor": Mock(),
        })

    def test_capped_read_sets_consumed_body_and_closes(self):
        raw = RawResponse(b"x" * 100)
        response = types.SimpleNamespace(raw=raw, close=raw.close)
        self.namespace()._capped(response, 8)
        self.assertEqual(response._content, b"x" * 8)
        self.assertTrue(response._content_consumed)
        self.assertTrue(raw.closed)

    @unittest.skipIf(real_requests is None, "requests transport is not installed")
    def test_actual_requests_response_closes_unread_socket_before_marking_consumed(self):
        for cap in (0, 8):
            with self.subTest(cap=cap):
                raw = RawResponse(b"x" * 100)
                response = real_requests.Response()
                response.raw = raw
                self.namespace()._capped(response, cap)
                self.assertTrue(raw.closed)
                self.assertEqual(response.content, b"x" * cap)
                self.assertTrue(response._content_consumed)
                self.assertEqual(raw.reads, [cap])

    def test_failed_cap_never_leaves_open_response_or_falls_back_to_full_read(self):
        raw = RawResponse(error=socket.timeout("timed out"))
        response = types.SimpleNamespace(raw=raw, close=raw.close)
        with self.assertRaises(http.RequestException):
            self.namespace()._capped(response, 8)
        self.assertTrue(raw.closed)
        self.assertEqual(raw.reads, [8])

    def test_zero_cap_is_passed_to_both_transports(self):
        for vendored in (False, True):
            with self.subTest(vendored=vendored):
                raw = RawResponse()
                response = types.SimpleNamespace(raw=raw, close=raw.close)
                session = Mock()
                session.request.return_value = response
                self.namespace(session, vendored).request("GET", raw.geturl(), max_bytes=0)
                kwargs = session.request.call_args.kwargs
                self.assertEqual(kwargs["headers"]["Accept-Encoding"], "identity")
                if vendored:
                    self.assertEqual(kwargs["max_bytes"], 0)
                else:
                    self.assertTrue(kwargs["stream"])
                    self.assertEqual(raw.reads, [0])
                    self.assertTrue(raw.closed)

    def test_probe_does_not_mutate_caller_headers(self):
        headers = {"User-Agent": "test"}
        raw = RawResponse()
        session = Mock()
        session.request.return_value = types.SimpleNamespace(raw=raw, close=raw.close)
        self.namespace(session).request("GET", raw.geturl(), max_bytes=4, headers=headers)
        self.assertEqual(headers, {"User-Agent": "test"})


class HosterProbeTests(unittest.TestCase):
    def probe(self, responses):
        request = Mock(side_effect=responses)
        module = functions_from("hosters.py", {"_check_one"}, {
            "request": request, "urlparse": urlparse,
            "DEFAULT_UA": "test", "log": Mock(), "format_exc": lambda: "error",
        })
        return module, request

    def test_live_page_probe_closes_after_status(self):
        response = Mock(status_code=200)
        module, request = self.probe([response])
        self.assertTrue(module._check_one("dlhd://1"))
        self.assertTrue(request.call_args.kwargs["stream"])
        response.close.assert_called_once()

    def test_head_fallback_closes_both_requests(self):
        head, body = Mock(status_code=405), Mock(status_code=206)
        module, request = self.probe([head, body])
        self.assertTrue(module._check_one("https://media.invalid/video"))
        head.close.assert_called_once()
        body.close.assert_called_once()
        self.assertTrue(request.call_args.kwargs["stream"])


class ResolverChoiceTests(unittest.TestCase):
    def resolve_voe(self, data):
        response = types.SimpleNamespace(
            text='<script type="application/json">"encoded"</script>',
            url="https://host.invalid/embed/id")
        module = functions_from("resolvers.py", {"resolve_voe"}, {
            "request": Mock(return_value=response), "_hdr": Mock(return_value={}),
            "_hdr_qs": lambda ref: "Referer=" + ref,
            "_voe_decode": lambda raw: data, "_json": json,
            "re": re, "urlparse": urlparse, "log": Mock(),
        })
        return module.resolve_voe(response.url)

    def test_adaptive_playlist_is_preferred_over_fixed_bitrate_mp4(self):
        stream = self.resolve_voe({"source": "https://media.invalid/master.m3u8",
                                  "direct_access_url": "https://media.invalid/video.mp4"})
        self.assertEqual(stream[0], "https://media.invalid/master.m3u8")

    def test_direct_file_remains_available_without_hls(self):
        stream = self.resolve_voe({"direct_access_url": "https://media.invalid/video.mp4"})
        self.assertEqual(stream[0], "https://media.invalid/video.mp4")


class FilemoonQualityTests(unittest.TestCase):
    def select(self, sources, limit=1080):
        module = functions_from("resolvers.py", {"_select_filemoon_source"}, {"urlparse": urlparse})
        return module._select_filemoon_source(sources, limit)

    def test_chooses_highest_known_resolution_within_cap(self):
        sources = [{"url": "https://media.invalid/4k.mp4", "height": 2160},
                   {"url": "https://media.invalid/hd.mp4", "height": "720"},
                   {"url": "https://media.invalid/fhd.mp4", "height": 1080}]
        self.assertEqual(self.select(sources), sources[2])
        self.assertEqual(self.select(sources, 720), sources[1])
        self.assertEqual(self.select(sources, 2160), sources[0])

    def test_preserves_master_playlist_for_adaptation(self):
        sources = [{"url": "https://media.invalid/full.mp4", "height": 1080},
                   {"url": "https://media.invalid/master.m3u8?token=2160", "bitrate_kbps": 12000}]
        self.assertEqual(self.select(sources), sources[1])

    def test_returns_none_when_all_known_sources_exceed_cap(self):
        self.assertIsNone(self.select([{"url": "https://media.invalid/movie.mp4", "height": 2160}]))

    def test_unknown_source_survives_without_guessing_from_signed_url(self):
        sources = [{"url": "https://media.invalid/high.mp4", "height": 2160},
                   {"url": "https://media.invalid/2160p.mp4?token=4k", "bitrate_kbps": 8000}]
        self.assertEqual(self.select(sources), sources[1])

    def test_invalid_entries_and_metadata_do_not_break_resolution(self):
        source = {"url": "https://media.invalid/movie.mp4", "height": "unknown"}
        self.assertEqual(self.select([None, {}, {"url": 123}, source]), source)
        self.assertIsNone(self.select([None, {}, {"url": ""}]))


if __name__ == "__main__":
    unittest.main()
