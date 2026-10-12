"""Synthetic public-player fixtures: no real host or Kodi dependency."""

import base64
import importlib.util
from pathlib import Path
import unittest
from urllib.parse import parse_qsl


PATH = (Path(__file__).resolve().parents[1] / "src" / "plugin.video.streamhub"
        / "resources" / "streamhub" / "host_support.py")
SPEC = importlib.util.spec_from_file_location("host_support_tested", PATH)
support = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(support)


class Response:
    def __init__(self, url, text="", status=200, content_type="text/html"):
        self.url = url
        self.text = text
        self.status_code = status
        self.headers = {"Content-Type": content_type}
        self.closed = False

    def close(self):
        self.closed = True


class HostSupportTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.responses = {}

    def page(self, url, body="", **kwargs):
        self.responses[url] = Response(url, body, **kwargs)
        return self.responses[url]

    def fetch(self, method, url, **kwargs):
        self.calls.append((url, kwargs))
        self.assertEqual(method, "GET")
        self.assertEqual(kwargs["max_bytes"], 1024 * 1024)
        self.assertEqual(kwargs["retries"], 0)
        self.assertEqual(kwargs["timeout"], 8)
        return self.responses[url]

    def resolve(self, url, **kwargs):
        result = support.resolve_public_embed(url, fetch=self.fetch, **kwargs)
        self.assertLessEqual(len(self.calls), support.MAX_PAGES)
        for current, _ in self.calls:
            self.assertTrue(self.responses[current].closed)
        return result

    def test_registry_uses_host_boundaries_and_current_aliases(self):
        examples = {
            "https://hglink.to/e/example": "StreamWish",
            "https://play.hgcloud.to/e/example": "StreamWish",
            "https://morencius.com/embed/example": "FileLions / VidHide",
            "https://streamhihi.com/e/example": "LuluStream",
            "https://uqload.vc/embed-example.html": "Uqload",
        }
        for url, family in examples.items():
            with self.subTest(url=url):
                self.assertEqual(support.family_for(url), family)
        for url in ("https://hglink.to.other.test/a", "https://evil.test/hglink.to",
                    "https://evil.test/?ref=uqload.vc", "https://uqload.vc@evil.test/e/a"):
            with self.subTest(url=url):
                self.assertIsNone(support.family_for(url))

    def test_video_source_relative_base_and_html_entities(self):
        url = "https://player.test/embed/123"
        self.page(url, '<base href="/media/"><video><source src="movie.mp4?a=1&amp;b=2" type="video/mp4"></video>')
        media, encoded = self.resolve(url)
        self.assertEqual(media, "https://player.test/media/movie.mp4?a=1&b=2")
        self.assertEqual(dict(parse_qsl(encoded))["Referer"], url)
        self.assertEqual(dict(parse_qsl(encoded))["Origin"], "https://player.test")

    def test_streamwish_jwplayer_prefers_hls_and_decodes_slashes(self):
        url = "https://hglink.to/e/abc"
        self.page(url, r'''<script>jwplayer("v").setup({sources:[{file:"https:\/\/cdn.test\/video.mp4"},{file:"\/hls\/master.m3u8?sig=a\u0026b=2"}]});</script>''')
        media, _ = self.resolve(url)
        self.assertEqual(media, "https://hglink.to/hls/master.m3u8?sig=a&b=2")

    def test_filelions_links_support_relative_extensionless_manifest(self):
        url = "https://morencius.com/embed/example"
        self.page(url, "<script>var links = {'hls2':'/low.m3u8','hls4':'/manifest?id=a%2Bb'};</script>")
        media, _ = self.resolve(url)
        self.assertEqual(media, "https://morencius.com/manifest?id=a%2Bb")

    def test_lulu_literal_sources_array(self):
        url = "https://streamhihi.com/e/example"
        self.page(url, '<script>var player = {sources: ["//cdn.test/stream.m3u8?x=1"]};</script>')
        self.assertEqual(self.resolve(url)[0], "https://cdn.test/stream.m3u8?x=1")

    def test_uqload_jwplayer_file_preserves_signed_query(self):
        url = "https://uqload.vc/embed-example.html"
        self.page(url, '<script>jwplayer("player").setup({file: "https://cdn.test/clip.mp4?token=a%2Bb%26c", image: "poster.jpg"});</script>')
        self.assertEqual(self.resolve(url)[0], "https://cdn.test/clip.mp4?token=a%2Bb%26c")

    def test_nested_advertising_file_and_arbitrary_links_are_not_sources(self):
        url = "https://player.test/e/example"
        self.page(url, '<a href="https://ad.test/trailer.mp4">ad</a><script>jwplayer("v").setup({advertising: {file: "https://ad.test/ad.mp4"}, file: "/film.mp4"});</script>')
        self.assertEqual(self.resolve(url)[0], "https://player.test/film.mp4")

    def test_two_iframe_hops_keep_full_parent_and_final_referers(self):
        first = "https://provider.test/watch/1"
        second = "https://player.test/embed/1"
        third = "https://player.test/player/1"
        self.page(first, '<iframe src="https://player.test/embed/1"></iframe>')
        self.page(second, '<iframe src="../player/1"></iframe>')
        self.page(third, '<video src="//cdn.test/stream.mp4"></video>')
        result = self.resolve(first, referer="https://catalog.test/movie/1")
        self.assertEqual(result[0], "https://cdn.test/stream.mp4")
        self.assertEqual(dict(parse_qsl(result[1]))["Referer"], third)
        self.assertEqual([call[1]["headers"]["Referer"] for call in self.calls],
                         ["https://catalog.test/movie/1", first, second])

    def test_iframe_loop_is_not_refetched(self):
        first, second = "https://player.test/a", "https://player.test/b"
        self.page(first, '<iframe src="/b"></iframe>')
        self.page(second, '<iframe src="/a#player"></iframe>')
        self.assertIsNone(self.resolve(first))
        self.assertEqual(len(self.calls), 2)

    def test_iframe_depth_is_limited_to_two(self):
        for index in range(4):
            self.page("https://player.test/%d" % index, '<iframe src="/%d"></iframe>' % (index + 1))
        self.assertIsNone(self.resolve("https://player.test/0"))
        self.assertEqual([url for url, _ in self.calls],
                         ["https://player.test/0", "https://player.test/1", "https://player.test/2"])

    def test_multiple_frames_have_total_budget_and_known_hosts_first(self):
        url = "https://provider.test/watch/1"
        self.page(url, '<iframe src="https://ads.test/a"></iframe><iframe src="https://hglink.to/e/abc"></iframe>')
        self.page("https://hglink.to/e/abc", '<video src="/movie.mp4"></video>')
        self.assertEqual(self.resolve(url)[0], "https://hglink.to/movie.mp4")
        self.assertEqual(len(self.calls), 2)

    def test_redirect_final_page_is_used_as_url_base(self):
        start = "https://old.test/e/abc"
        response = self.page(start, '<video src="media.mp4"></video>')
        response.url = "https://current.test/embed/abc"
        self.assertEqual(self.resolve(start)[0], "https://current.test/embed/media.mp4")

    def test_direct_media_response_keeps_incoming_referer(self):
        url = "https://cdn.test/stream?id=123"
        self.page(url, content_type="application/vnd.apple.mpegurl")
        media, encoded = self.resolve(url, referer="https://player.test/e/123")
        self.assertEqual(media, url)
        self.assertEqual(dict(parse_qsl(encoded))["Referer"], "https://player.test/e/123")

    def test_legacy_var_url_assignment(self):
        url = "https://player.test/e/123"
        self.page(url, "<script>var url = 'https://cdn.test/master.m3u8';</script>")
        self.assertEqual(self.resolve(url)[0], "https://cdn.test/master.m3u8")

    def test_one_layer_base64_source_without_execution(self):
        url = "https://player.test/e/123"
        encoded = base64.b64encode(b'https://cdn.test/movie.mp4?token=abc').decode()
        self.page(url, '<script>var player = atob("%s");</script>' % encoded)
        self.assertEqual(self.resolve(url)[0], "https://cdn.test/movie.mp4?token=abc")

    def test_base64_nested_layers_are_not_evaluated(self):
        url = "https://player.test/e/123"
        inner = base64.b64encode(b'https://cdn.test/movie.mp4').decode()
        outer = base64.b64encode(('atob("%s")' % inner).encode()).decode()
        self.page(url, '<script>atob("%s");</script>' % outer)
        self.assertIsNone(self.resolve(url))

    def test_existing_packer_is_used_through_injected_function(self):
        url = "https://player.test/e/123"
        self.page(url, "<script>eval(function(p,a,c,k,e,d){return p;}('0',2,1,'file'.split('|'),0,{}));</script>")
        seen = []
        def unpacker(text):
            seen.append(text)
            return 'jwplayer("v").setup({file:"/public.mp4"});'
        self.assertEqual(self.resolve(url, unpacker=unpacker)[0], "https://player.test/public.mp4")
        self.assertEqual(len(seen), 1)

    def test_excessive_packer_count_is_not_executed(self):
        url = "https://player.test/e/123"
        self.page(url, "<script>eval(function(p,a,c,k,e,d){return p;}('0',62,99999999,'file'.split('|'),0,{}));</script>")
        def forbidden(text):
            self.fail("oversized packer must not run")
        self.assertIsNone(self.resolve(url, unpacker=forbidden))

    def test_http_errors_and_challenge_pages_are_not_followed(self):
        for status, text in ((403, '<video src="/movie.mp4">'),
                             (200, '<script>challenge-platform</script><iframe src="/next"></iframe>')):
            with self.subTest(status=status):
                self.calls.clear()
                self.page("https://player.test/e/123", text, status=status)
                self.assertIsNone(self.resolve("https://player.test/e/123"))
                self.assertEqual(len(self.calls), 1)

    def test_unsafe_schemes_credentials_and_expressions_are_ignored(self):
        url = "https://player.test/e/123"
        self.page(url, '''<iframe src="javascript:alert(1)"></iframe><source src="file:///tmp/movie.mp4"><video src="https://user:pass@cdn.test/movie.mp4"><script>var url="https://cdn.test/" + token + ".mp4";</script>''')
        self.assertIsNone(self.resolve(url))
        self.assertEqual(len(self.calls), 1)

    def test_commented_player_config_is_not_a_source(self):
        url = "https://player.test/e/123"
        self.page(url, "<script>/* sources:[{file:'https://ad.test/ad.mp4'}] */ var url='https://cdn.test/movie.mp4';</script>")
        self.assertEqual(self.resolve(url)[0], "https://cdn.test/movie.mp4")

    def test_plain_text_hls_response_is_playable(self):
        url = "https://cdn.test/manifest?id=123"
        self.page(url, "#EXTM3U\n#EXT-X-TARGETDURATION:6\npart.ts", content_type="text/plain")
        self.assertEqual(self.resolve(url)[0], url)

    def test_unknown_unstructured_page_returns_none(self):
        url = "https://player.test/e/123"
        self.page(url, '<img src="poster.jpg"><a href="https://ads.test/ad.mp4">ad</a>')
        self.assertIsNone(self.resolve(url))

    def test_page_limit_applies_even_when_injected_fetch_ignores_cap(self):
        url = "https://player.test/e/123"
        self.page(url, ' ' * support.MAX_PAGE_BYTES + '<video src="/movie.mp4">')
        self.assertIsNone(self.resolve(url))


if __name__ == "__main__":
    unittest.main()
