"""Synthetic Dood page fixtures: no provider or media requests are made."""
import ast
import base64
from http.cookiejar import Cookie, CookieJar
from pathlib import Path
import re
import sys
import traceback
import types
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qsl, quote, urlparse

SOURCE = Path(__file__).resolve().parents[1] / "src/plugin.video.streamhub/resources/streamhub/resolvers.py"


def load_resolvers():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    tree.body = [node for node in tree.body
                 if not (isinstance(node, ast.ImportFrom) and node.module == "streamhub.utils")]
    namespace = {"re": re, "base64": base64, "quote": quote, "urlparse": urlparse,
                 "request": Mock(), "log": Mock(), "format_exc": traceback.format_exc}
    exec(compile(tree, str(SOURCE), "exec"), namespace)
    namespace["mark_blocked"] = Mock()
    namespace["mark_ok"] = Mock()
    namespace["is_blocked"] = Mock(return_value=False)
    namespace["_time"] = types.SimpleNamespace(time=lambda: 1700000000.125)
    namespace["_random"] = types.SimpleNamespace(choice=lambda chars: "A")
    return namespace


def page(url, text, status=200):
    return types.SimpleNamespace(url=url, text=text, status_code=status)


class DoodTests(unittest.TestCase):
    def setUp(self):
        self.module = load_resolvers()
        self.request = self.module["request"]
        self.embed = "https://playmogo.com/e/AbC123"
        self.endpoint = "https://playmogo.com/pass_md5/challenge/Token_9"
        self.html = "$.get('/pass_md5/challenge/Token_9', function(data) {});"

    def resolve(self, responses, url=None):
        self.request.side_effect = responses
        result = self.module["resolve_dood"](url or self.embed)
        self.assertFalse(self.module["log"].called, self.module["log"].call_args)
        return result

    def test_supported_aliases_and_no_matches_in_query_or_lookalike_hostname(self):
        check = self.module["is_dood_url"]
        for host in ("do7go.com", "ds2video.com", "dsvplay.com", "d0o0d.com", "doply.net",
                     "vide0.net", "vvide0.com", "d-s.io", "myvidplay.com", "www.dood.to"):
            with self.subTest(host=host):
                self.assertTrue(check("https://" + host + "/e/AbC123"))
        self.assertFalse(check("https://other.invalid/?src=https://do7go.com/e/AbC123"))
        self.assertFalse(check("https://do7go.com.other.invalid/e/AbC123"))
        self.assertFalse(check("javascript://do7go.com/e/AbC123"))

    def test_old_alias_uses_current_player_without_reencoding_query(self):
        normalize = self.module["_dood_page_url"]
        self.assertEqual(normalize("http://do7go.com/e/AbC123?token=a%2Bb&x=1&x=2"),
                         "https://playmogo.com/e/AbC123?token=a%2Bb&x=1&x=2")
        self.assertEqual(normalize("https://myvidplay.com/d/AbC123"),
                         "https://myvidplay.com/d/AbC123")

    def test_legacy_prefix_generates_nonce_and_milliseconds_with_full_referer(self):
        result = self.resolve([page(self.embed, self.html),
                               page(self.endpoint, "https://cdn.invalid/media/prefix~")])
        self.assertEqual(result[0], "https://cdn.invalid/media/prefix~AAAAAAAAAA?token=Token_9&expiry=1700000000125")
        headers = dict(parse_qsl(result[1]))
        self.assertEqual(headers["Referer"], self.embed)
        self.assertEqual(self.request.call_args.kwargs["headers"]["Referer"], self.embed)
        self.assertEqual(self.request.call_args_list[0].kwargs["max_bytes"], 1024 * 1024)
        self.assertEqual(self.request.call_args_list[1].kwargs["max_bytes"], 16384)

    def test_public_player_query_suffix_is_preserved(self):
        html = self.html + '''function makePlay(data) {return data + '?token=AbC_-9%2B&extra=a%2Fb&expiry=' + Date.now();}'''
        result = self.resolve([page(self.embed, html), page(self.endpoint, "https://cdn.invalid/prefix~")])
        self.assertTrue(result[0].endswith("?token=AbC_-9%2B&extra=a%2Fb&expiry=1700000000125"))

    def test_fallback_token_does_not_include_pass_endpoint_query(self):
        html = "$.get('/pass_md5/key/AbC%2B_-9?cache=123', function(data) {});"
        result = self.resolve([page(self.embed, html), page(self.endpoint, "https://cdn.invalid/prefix~")])
        self.assertEqual(dict(parse_qsl(urlparse(result[0]).query))["token"], "AbC+_-9")
        self.assertNotIn("cache=", result[0])

    def test_complete_signed_storage_url_remains_byte_for_byte_unchanged(self):
        direct = "https://bucket.cloudflarestorage.com/object?X-Signature=AbC%2B%2F&X-Expires=100&x=1&x=2"
        result = self.resolve([page(self.embed, self.html), page(self.endpoint, direct)])
        self.assertEqual(result[0], direct)
        self.assertEqual(self.request.call_count, 2)

    def test_cloud_storage_object_without_query_is_complete(self):
        direct = "https://bucket.cloudflarestorage.com/opaque-object"
        result = self.resolve([page(self.embed, self.html), page(self.endpoint, direct)])
        self.assertEqual(result[0], direct)

    def test_download_page_follows_public_iframe_and_retains_parent_referer(self):
        download = "https://playmogo.com/d/AbC123"
        result = self.resolve([page(download, '<iframe allowfullscreen src="/e/Protected456"></iframe>'),
                               page("https://playmogo.com/e/Protected456", self.html),
                               page(self.endpoint, "https://cdn.invalid/video.mp4")], download)
        self.assertEqual(result[0], "https://cdn.invalid/video.mp4")
        self.assertEqual(dict(parse_qsl(result[1]))["Referer"], download)
        self.assertEqual(self.request.call_args_list[1].args[1], "https://playmogo.com/e/Protected456")

    def test_download_page_without_iframe_uses_embed_path(self):
        download = "https://playmogo.com/d/AbC123?view=1"
        embed = "https://playmogo.com/e/AbC123?view=1"
        result = self.resolve([page(download, "<html>player</html>"), page(embed, self.html),
                               page(self.endpoint, "https://cdn.invalid/video.mp4")], download)
        self.assertIsNotNone(result)
        self.assertEqual(self.request.call_args_list[1].args[1], embed)

    def test_ad_iframe_is_not_followed(self):
        result = self.resolve([page(self.embed, '<iframe src="https://ads.invalid/pop"></iframe>')])
        self.assertIsNone(result)
        self.assertEqual(self.request.call_count, 1)

    def test_challenge_stops_without_fallback_or_token_request(self):
        result = self.resolve([page(self.embed, "Just a moment: challenge-platform", 403)])
        self.assertIsNone(result)
        self.assertEqual(self.request.call_count, 1)
        self.module["mark_blocked"].assert_not_called()
        self.assertEqual(self.module["get_resolve_failure"](self.embed), "protected")

    def test_redirected_challenge_is_reported_for_same_file_across_dood_aliases(self):
        original = "https://doodstream.com/e/AbC123"
        self.resolve([page(self.embed, "Just a moment: cf_chl_opt", 403)], original)
        failure = self.module["get_resolve_failure"]
        self.assertEqual(failure(original), "protected")
        self.assertEqual(failure("https://do7go.com/d/AbC123"), "protected")
        self.assertIsNone(failure("https://doodstream.com/e/Other456"))
        self.assertIsNone(failure("https://unrelated.invalid/e/AbC123"))
        self.module["mark_blocked"].assert_not_called()

    def test_http_200_captcha_stops_generic_fallback(self):
        captcha = '<form><div class="cf-turnstile" data-sitekey="fixture"></div></form>'
        self.request.side_effect = [page(self.embed, captcha)]
        generic = self.module["resolve_generic"] = Mock()
        self.assertIsNone(self.module["resolve"](self.embed))
        self.assertEqual(self.module["get_resolve_failure"](self.embed), "protected")
        self.assertEqual(self.request.call_count, 1)
        generic.assert_not_called()

    def test_pass_endpoint_challenge_marks_original_file(self):
        result = self.resolve([page(self.embed, self.html),
                               page(self.endpoint, 'Just a moment: cf_chl_opt', 403)])
        self.assertIsNone(result)
        self.assertEqual(self.module["get_resolve_failure"](self.embed), "protected")

    def test_retry_can_recover_and_clear_protection_diagnostic(self):
        self.resolve([page(self.embed, "Just a moment: challenge-platform", 403)])
        result = self.resolve([page(self.embed, self.html),
                               page(self.endpoint, "https://cdn.invalid/video.mp4")])
        self.assertIsNotNone(result)
        self.assertIsNone(self.module["get_resolve_failure"](self.embed))

    def test_protection_diagnostic_expires_and_stays_bounded(self):
        self.resolve([page(self.embed, "Just a moment", 403)])
        self.module["_time"].time = lambda: 1700000301
        self.assertIsNone(self.module["get_resolve_failure"](self.embed))
        for i in range(140):
            self.module["_set_resolve_failure"]("https://playmogo.com/e/ID%d" % i, "protected")
        self.assertEqual(len(self.module["_RESOLVE_FAILURES"]), 128)

    def test_plain_http_error_is_not_mislabeled_as_captcha(self):
        self.assertIsNone(self.resolve([page(self.embed, "File not found", 404)]))
        self.assertIsNone(self.module["get_resolve_failure"](self.embed))

    def test_failed_pass_request_does_not_create_playable_url(self):
        result = self.resolve([page(self.embed, self.html), page(self.endpoint, "https://cdn.invalid/prefix", 500)])
        self.assertIsNone(result)
        self.module["mark_ok"].assert_not_called()

    def test_invalid_endpoint_body_is_rejected(self):
        for body in ("<html>Not found</html>", "javascript:alert(1)", "https://cdn.invalid/path with space", "https://cdn.invalid/file|Cookie=x"):
            with self.subTest(body=body):
                self.assertIsNone(self.module["_dood_media_url"](body, self.html, self.endpoint))

    def test_cookie_transfer_obeys_cdn_scope(self):
        jar = CookieJar()
        for domain, name in (("cdn.invalid", "media"), ("playmogo.com", "session"), ("other.invalid", "other")):
            jar.set_cookie(Cookie(0, name, "fixture", None, False, domain, False, False,
                                  "/", True, True, None, True, None, None, {}))
        stub = types.ModuleType("streamhub.utils")
        stub.session = types.SimpleNamespace(cookies=jar)
        with patch.dict(sys.modules, {"streamhub.utils": stub}):
            headers = dict(parse_qsl(self.module["_dood_headers"](self.embed, "https://cdn.invalid/video.mp4")))
        self.assertEqual(headers["Cookie"], "media=fixture")
        self.assertEqual(headers["Referer"], self.embed)

    def test_generic_can_be_disabled_for_external_resolver_handoff(self):
        generic = Mock()
        self.module["resolve_generic"] = generic
        self.assertIsNone(self.module["resolve"]("https://other.invalid/e/123", generic=False))
        generic.assert_not_called()


if __name__ == "__main__":
    unittest.main()
