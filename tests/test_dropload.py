"""Offline public-player and protection fixtures; no live media requests."""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qsl

from test_dood import load_resolvers, page

SUPPORT = Path(__file__).resolve().parents[1] / "src/plugin.video.streamhub/resources/streamhub/host_support.py"
spec = importlib.util.spec_from_file_location("streamhub.host_support", SUPPORT)
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)


class DroploadTests(unittest.TestCase):
    def setUp(self):
        self.module = load_resolvers()
        self.request = self.module["request"]
        self.embed = "https://dr0pstream.com/e/AbC123"

    def resolve(self, html, url=None, final_url=None, status=200):
        self.request.return_value = page(final_url or self.embed, html, status)
        with patch.dict(sys.modules, {"streamhub.host_support": support}):
            result = self.module["resolve_dropload"](url or self.embed, "https://provider.invalid/title")
        self.assertFalse(self.module["log"].called, self.module["log"].call_args)
        return result

    def test_all_documented_aliases_use_hostname_boundaries(self):
        check = self.module["is_dropload_url"]
        for host in ("dropload.io", "dropload.tv", "dropload.pro", "dropload.co", "dr0pstream.com", "www.dr0pstream.com"):
            with self.subTest(host=host):
                self.assertTrue(check("https://" + host + "/e/AbC123"))
        for url in ("https://dr0pstream.com.other.invalid/e/AbC123", "https://other.invalid/?host=dr0pstream.com",
                    "javascript://dropload.io/e/AbC123"):
            self.assertFalse(check(url))

    def test_supported_public_path_forms_normalize_without_reencoding_query(self):
        normalize = self.module["_dropload_page_url"]
        for path in ("/AbC123", "/embed-AbC123.html", "/d/AbC123", "/e/AbC123", "/e/AbC123/"):
            self.assertEqual(normalize("https://dropload.io" + path + "?token=a%2Bb&x=1&x=2"),
                             "https://dropload.io/e/AbC123?token=a%2Bb&x=1&x=2")
        for url in ("https://dropload.io/", "https://dropload.io/settings/profile", "https://other.invalid/AbC123"):
            self.assertIsNone(normalize(url))

    def test_signed_extensionless_source_and_redirected_origin_headers(self):
        direct = "https://cdn.invalid/object?token=a%2Bb%2F&x=1&x=2"
        result = self.resolve('sources: [{file: "' + direct + '"}]', final_url="https://dropload.tv/e/AbC123")
        self.assertEqual(result[0], direct)
        headers = dict(parse_qsl(result[1]))
        self.assertEqual(headers["Origin"], "https://dropload.tv")
        self.assertEqual(headers["Referer"], "https://dropload.tv/")
        self.assertEqual(self.request.call_count, 1)
        self.assertEqual(self.request.call_args.args[0], "GET")
        self.assertEqual(self.request.call_args.kwargs["max_bytes"], 1024 * 1024)
        self.assertEqual(self.request.call_args.kwargs["headers"]["Referer"], "https://provider.invalid/title")

    def test_adaptive_source_preferred_over_direct_rendition(self):
        html = 'sources: [{file:"https://cdn.invalid/video.mp4"}, {type:"hls", file:"//cdn.invalid/manifest?sig=a%2Bb"}]'
        self.assertEqual(self.resolve(html)[0], "https://cdn.invalid/manifest?sig=a%2Bb")

    def test_escaped_player_source_is_decoded_without_script_execution(self):
        html = r'sources: [{"file": "https:\/\/cdn.invalid\/master.m3u8?x=1&amp;y=2"}]'
        self.assertEqual(self.resolve(html)[0], "https://cdn.invalid/master.m3u8?x=1&y=2")

    def test_loose_advertisement_media_is_not_a_player_source(self):
        self.assertIsNone(self.resolve('var source="https://ads.invalid/sample.mp4";'))

    def test_commented_player_sources_do_not_override_current_video(self):
        html = ('/* sources:[{file:"https://ads.invalid/obsolete.m3u8"}] */'
                'sources:[{file:"https://cdn.invalid/current.mp4"}]')
        self.assertEqual(self.resolve(html)[0], "https://cdn.invalid/current.mp4")

    def test_packed_literal_player_can_be_read_statically(self):
        html = "eval(function(p,a,c,k,e,d){return p;}('0:[{1:\"2://3/4.5\"}]',6,6,'sources|file|https|cdn.invalid|video|mp4'.split('|'),0,{}))"
        self.assertEqual(self.resolve(html)[0], "https://cdn.invalid/video.mp4")

    def test_turnstile_200_response_never_submits_form_or_returns_decoy(self):
        html = ('<script src="https://challenges.cloudflare.com/turnstile/v0/api.js?compat=recaptcha"></script>'
                '<form method="post"><div class="g-recaptcha" data-sitekey="fixture"></div></form>'
                'sources: [{file:"https://ads.invalid/sample.mp4"}]')
        self.assertIsNone(self.resolve(html))
        self.assertEqual(self.module["get_resolve_failure"](self.embed), "protected")
        self.assertEqual(self.request.call_count, 1)
        self.module["mark_blocked"].assert_not_called()
        self.assertIsNone(self.module["get_resolve_failure"]("https://dr0pstream.com/e/Other456"))

    def test_protection_does_not_match_ordinary_player_script_mentions(self):
        html = '// turnstile, g-recaptcha may appear in site documentation\nsources: [{file:"https://cdn.invalid/video.mp4"}]'
        self.assertIsNotNone(self.resolve(html))
        self.assertIsNone(self.module["get_resolve_failure"](self.embed))

    def test_failed_response_and_unsafe_media_are_rejected(self):
        self.assertIsNone(self.resolve('sources: [{file:"https://cdn.invalid/video.mp4"}]', status=404))
        for direct in ("javascript:alert(1)", "https://cdn.invalid/video.mp4|Cookie=x", "https://user:pass@cdn.invalid/video.mp4"):
            with self.subTest(direct=direct):
                self.assertIsNone(self.resolve('sources: [{file:"' + direct + '"}]'))

    def test_native_dispatch_handles_dr0pstream_without_generic_fallback(self):
        resolver = self.module["resolve_dropload"] = Mock(return_value=None)
        generic = self.module["resolve_generic"] = Mock()
        self.assertIsNone(self.module["resolve"](self.embed, "https://provider.invalid"))
        resolver.assert_called_once_with(self.embed, "https://provider.invalid")
        generic.assert_not_called()


if __name__ == "__main__":
    unittest.main()
