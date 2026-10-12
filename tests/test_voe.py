"""Offline regression for VOE mirror routing before generic sample URLs."""
import ast
import base64
import codecs
import json
from pathlib import Path
import re
import traceback
import types
import unittest
from unittest.mock import Mock
from urllib.parse import quote, urlparse

SOURCE = Path(__file__).resolve().parents[1] / "src/plugin.video.streamhub/resources/streamhub/resolvers.py"


def load_resolvers():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    tree.body = [node for node in tree.body
                 if not (isinstance(node, ast.ImportFrom) and node.module == "streamhub.utils")]
    namespace = {"re": re, "base64": base64, "quote": quote, "urlparse": urlparse,
                 "request": Mock(), "log": Mock(), "format_exc": traceback.format_exc}
    exec(compile(tree, str(SOURCE), "exec"), namespace)
    namespace["is_blocked"] = Mock(return_value=False)
    return namespace


def payload(data):
    reversed_data = base64.b64encode(json.dumps(data).encode("utf-8"))[::-1]
    shifted = bytes((byte + 3) % 256 for byte in reversed_data)
    return codecs.encode(base64.b64encode(shifted).decode("ascii"), "rot_13")


class VoeRoutingTests(unittest.TestCase):
    def test_actual_mirror_families_route_to_native_decoder_before_sample_url(self):
        expected = "https://media.invalid/movie/master.m3u8?signature=fixture"
        encoded = payload({"source": expected, "direct_access_url": "https://media.invalid/movie.mp4"})
        html = ('<script>var source="https://sample.invalid/Big_Buck_Bunny_10s.mp4";</script>'
                '<script type="application/json">' + json.dumps([encoded]) + '</script>')
        for host in ("goofy-banana.com", "megankiddifference.com"):
            with self.subTest(host=host):
                module = load_resolvers()
                module["request"].return_value = types.SimpleNamespace(
                    text=html, url="https://megankiddifference.com/e/fixture", status_code=200)
                generic = Mock(return_value=("https://sample.invalid/Big_Buck_Bunny_10s.mp4", ""))
                module["resolve_generic"] = generic
                stream = module["resolve"]("https://" + host + "/e/fixture", generic=False)
                self.assertIsNotNone(stream)
                self.assertEqual(stream[0], expected)
                generic.assert_not_called()
                self.assertFalse(module["log"].called)

    def test_mirror_names_only_match_hostname_boundaries(self):
        module = load_resolvers()
        check = module["is_voe_url"]
        self.assertTrue(check("https://www.goofy-banana.com/e/fixture"))
        self.assertTrue(check("https://megankiddifference.com/e/fixture"))
        self.assertFalse(check("https://unrelated.invalid/?next=goofy-banana.com"))
        self.assertFalse(check("https://goofy-banana.com.unrelated.invalid/e/fixture"))
        self.assertFalse(check("https://notgoofy-banana.com/e/fixture"))

    def test_page_fetches_are_bounded_and_redirect_cycles_stop(self):
        module = load_resolvers()
        first = "https://goofy-banana.com/e/fixture"
        second = "https://megankiddifference.com/e/fixture"
        module["request"].side_effect = [
            types.SimpleNamespace(text="window.location.href = '%s';" % second, url=first, status_code=200),
            types.SimpleNamespace(text="window.location.href = '%s';" % first, url=second, status_code=200)]
        self.assertIsNone(module["resolve_voe"](first))
        self.assertEqual(module["request"].call_count, 2)
        for call in module["request"].call_args_list:
            self.assertEqual(call.kwargs["max_bytes"], 1024 * 1024)
            self.assertEqual(call.kwargs["timeout"], 8)
            self.assertEqual(call.kwargs["retries"], 1)

    def test_decoder_retains_direct_fallback_when_playlist_missing(self):
        module = load_resolvers()
        direct = "https://media.invalid/movie.mp4"
        encoded = payload({"direct_access_url": direct})
        module["request"].return_value = types.SimpleNamespace(
            text='<script type="application/json">' + json.dumps([encoded]) + '</script>',
            url="https://megankiddifference.com/e/fixture", status_code=200)
        stream = module["resolve"]("https://goofy-banana.com/e/fixture", generic=False)
        self.assertEqual(stream[0], direct)


if __name__ == "__main__":
    unittest.main()
