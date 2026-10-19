"""New provider regression tests; no network and no real Kodi imports."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

DIRECTORY = Path(__file__).resolve().parents[1] / "src/plugin.video.streamhub/resources/lib/scrapers"


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        utils = types.ModuleType("streamhub.utils")
        utils.request = Mock(side_effect=AssertionError("unexpected network"))
        utils.log = Mock()
        utils.cachepath = self.temp.name
        utils.format_exc = lambda: "test traceback"
        package = types.ModuleType("lib.scrapers")
        package.__path__ = [str(DIRECTORY)]
        package.register = lambda cls: cls
        modules = {"streamhub.utils": utils, "lib": types.ModuleType("lib"),
                   "lib.scrapers": package}
        for name in ("base", "html_catalog", "hdfilme", "streamkiste", "ramoflix"):
            modules["lib.scrapers." + name] = None
        self.patch = patch.dict(sys.modules, modules)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        for name in ("base", "html_catalog", "hdfilme", "streamkiste", "ramoflix"):
            spec = importlib.util.spec_from_file_location("lib.scrapers." + name, DIRECTORY / (name + ".py"))
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            setattr(self, name, module)
        self.hd = self.hdfilme.HDFilmeScraper()
        self.sk = self.streamkiste.StreamKisteScraper()

    def test_hd_search_entities_year_lazy_image_and_query_encoding(self):
        self.hd.fetch_text = Mock(return_value='''<div class="movie-short-card">
            <img src="/placeholder.jpg" data-src="/poster.jpg">
            <div class="movie-title"><h3><a href="/123-example.html">Example &amp; Friends</a></h3></div>
            <div class="movie-meta">2003 Stream</div></div>''')
        result = self.hd.search("Example & Friends")
        self.assertEqual(result[0]["title"], "Example & Friends")
        self.assertEqual(result[0]["year"], 2003)
        self.assertEqual(result[0]["poster"], "https://hd-filme.lat/poster.jpg")
        self.assertIn("story=Example+%26+Friends", self.hd.fetch_text.call_args.args[0])
        self.assertEqual(self.hd.fetch_text.call_args.kwargs["max_bytes"], 1024 * 1024)

    def test_hd_search_rejects_external_cards_series_and_duplicates(self):
        card = '<div class="movie-short-card"><div class="movie-title"><a href="%s">%s</a></div></div>'
        self.hd.fetch_text = Mock(return_value="".join([
            card % ("/1-film.html", "Film"), card % ("/1-film.html", "Film"),
            card % ("https://external.invalid/1-film.html", "Bad"),
            card % ("/2-show.html", "Example Staffel 1")]))
        self.assertEqual(len(self.hd.search("Film")), 1)
        self.hd.fetch_text.reset_mock()
        self.assertEqual(self.hd.search("Example", "tvshow"), [])
        self.hd.fetch_text.assert_not_called()

    def test_hd_sources_only_explicit_mirrors_no_trailer_or_ads(self):
        self.hd.fetch_text = Mock(return_value='''<a data-link="https://ads.invalid/">ad</a>
            <ul class="mirrors" id="fsMirrors">
            <li data-link="https://voe.sx/e/example">Vega</li>
            <li data-link="https://voe.sx/e/example">Duplicate</li>
            <li data-link="https://youtube.com/embed/example">Trailer</li>
            <li data-link="javascript:bad()">Bad</li></ul>''')
        sources = self.hd.get_sources({"url": "https://hd-filme.lat/1-film.html"})
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["host"], "voe.sx")
        self.assertEqual(sources[0]["referer"], "https://hd-filme.lat/1-film.html")
        self.assertEqual(sources[0]["provider"], "hdfilme")

    def test_hd_does_not_fetch_unrelated_item_urls(self):
        self.hd.fetch_text = Mock()
        self.assertEqual(self.hd.get_sources({"url": "https://external.invalid/"}), [])
        self.hd.fetch_text.assert_not_called()

    def test_kiste_search_filters_movie_routes(self):
        def card(url, title):
            return '<div class="movie-preview col-7"><img src="/poster.jpg"><span class="movie-title"><a href="%s">%s</a></span><span class="movie-release">1999 - Action</span></div>' % (url, title)
        self.sk.fetch_text = Mock(return_value=card("/movie/123-film.html", "Film") +
                                  card("/serial/456-show.html", "Show") +
                                  card("/movie/123-film.html", "Film"))
        result = self.sk.search("Film")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["year"], 1999)
        self.assertEqual(result[0]["url"], "https://streamkiste.bid/movie/123-film.html")

    def test_kiste_unwraps_public_sources_and_preserves_parent_referer(self):
        self.sk.fetch_text = Mock(side_effect=[
            '<iframe src="https://devideosrc.co/embed/download/tt12345"></iframe><iframe src="https://devideosrc.co/movie/tt12345"></iframe>',
            '<script>fetch("/api/embed-links", {body: JSON.stringify({type: \'movie\', id: "tt12345", token: "abc.123"})});</script>'])
        self.sk.fetch_json = Mock(return_value={"ok": True, "sources": [
            {"name": "Mixdrop", "url": "https://mxdrop.to/e/example", "embed": True},
            {"name": "Duplicate", "url": "https://mxdrop.to/e/example", "kind": "embed"},
            {"name": "Bad", "url": "javascript:bad()", "embed": True}]})
        result = self.sk.get_sources({"url": "https://streamkiste.bid/movie/1-film.html"})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["url"], "https://mxdrop.to/e/example")
        self.assertEqual(result[0]["referer"], "https://devideosrc.co/movie/tt12345")
        self.assertEqual(result[0]["language"], "DE")
        call = self.sk.fetch_json.call_args
        self.assertEqual(call.args[0], "https://devideosrc.co/api/embed-links")
        self.assertEqual(json.loads(call.kwargs["data"]), {"type": "movie", "id": "tt12345", "token": "abc.123"})
        self.assertEqual(call.kwargs["method"], "POST")

    def test_kiste_missing_or_wrong_public_payload_does_not_guess_sources(self):
        self.sk.fetch_json = Mock()
        for html in ("<html>captcha</html>", '<script>body:JSON.stringify({type:"movie",id:"tt0000",token:"abc"})</script>'):
            self.sk.fetch_text = Mock(return_value=html)
            self.assertEqual(self.sk._embed_sources("https://devideosrc.co/movie/tt12345"), [])
        self.sk.fetch_json.assert_not_called()

    def test_kiste_does_not_follow_other_iframes_or_download_links(self):
        self.sk.fetch_text = Mock(return_value='<iframe src="https://ads.invalid/"></iframe><iframe src="https://devideosrc.co/embed/download/tt12345"></iframe>')
        self.sk.fetch_json = Mock()
        self.assertEqual(self.sk.get_sources({"url": "/movie/1-film.html"}), [])
        self.assertEqual(self.sk.fetch_text.call_count, 1)
        self.sk.fetch_json.assert_not_called()

    def test_kiste_api_failure_returns_no_sources(self):
        self.sk.fetch_text = Mock(return_value='<script>body:JSON.stringify({type:"movie",id:"tt12345",token:"abc"})</script>')
        self.sk.fetch_json = Mock(return_value={"ok": False, "sources": []})
        self.assertEqual(self.sk._embed_sources("https://devideosrc.co/movie/tt12345"), [])

    def test_empty_searches_and_unsupported_series_do_not_fetch(self):
        for scraper in (self.hd, self.sk):
            scraper.fetch_text = Mock()
            self.assertEqual(scraper.search(""), [])
            self.assertEqual(scraper.search("Show", "tvshow"), [])
            scraper.fetch_text.assert_not_called()

    def test_ramoflix_unknown_or_error_page_does_not_poison_type_cache(self):
        scraper = self.ramoflix.RamoflixScraper()
        for status, body in ((403, "var Servers = {};"),
                             (200, "<html>Verification required</html>")):
            with self.subTest(status=status):
                scraper.fetch = Mock(return_value=types.SimpleNamespace(status_code=status, text=body))
                self.assertIsNone(scraper.page_kind("https://ramoflix.net/film/"))
                self.assertIsNone(self.base.cache_get("k|ramoflix|https://ramoflix.net/film/"))
        scraper.fetch = Mock(return_value=types.SimpleNamespace(status_code=200, text="var Servers = {};"))
        self.assertEqual(scraper.page_kind("https://ramoflix.net/film/"), "movie")
        self.assertEqual(self.base.cache_get("k|ramoflix|https://ramoflix.net/film/"), "movie")

    def test_ramoflix_type_check_propagates_cancellation(self):
        scraper = self.ramoflix.RamoflixScraper()
        scraper.fetch = Mock(side_effect=self.base.Cancelled())
        with self.assertRaises(self.base.Cancelled):
            scraper.page_kind("https://ramoflix.net/film/")

    def test_html_url_helper_rejects_non_http_and_credentials(self):
        helper = self.html_catalog.http_url
        self.assertEqual(helper("https://example.com", "//cdn.example.com/image.jpg"),
                         "https://cdn.example.com/image.jpg")
        self.assertEqual(helper("https://example.com", "javascript:alert(1)"), "")
        self.assertEqual(helper("https://example.com", "https://[invalid/"), "")
        self.assertEqual(helper("https://example.com", "https://user:pass@example.com"), "")
        root = self.html_catalog.parse_html('<div><!--<iframe src="https://bad"/>--><br><span>A &amp; B</span></div>')
        self.assertIsNone(root.find_tag("iframe"))
        self.assertEqual(root.text(), "A & B")


if __name__ == "__main__":
    unittest.main()
