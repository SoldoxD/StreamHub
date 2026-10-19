"""Fixture tests for the public Movie2k and Einschalten catalog contracts."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch


DIRECTORY = Path(__file__).resolve().parents[1] / "src/plugin.video.streamhub/resources/lib/scrapers"


class FMHYProviderTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        utils = types.ModuleType("streamhub.utils")
        utils.request = Mock(side_effect=AssertionError("unexpected network"))
        utils.log = Mock()
        utils.cachepath = temporary.name
        utils.format_exc = lambda: "test traceback"
        package = types.ModuleType("lib.scrapers")
        package.__path__ = [str(DIRECTORY)]
        package.register = lambda cls: cls
        modules = {"streamhub.utils": utils, "lib": types.ModuleType("lib"),
                   "lib.scrapers": package}
        for name in ("base", "html_catalog", "movie2k", "einschalten"):
            modules["lib.scrapers." + name] = None
        patcher = patch.dict(sys.modules, modules)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("base", "html_catalog", "movie2k", "einschalten"):
            spec = importlib.util.spec_from_file_location("lib.scrapers." + name, DIRECTORY / (name + ".py"))
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            setattr(self, name, module)
        self.m2k = self.movie2k.Movie2kScraper()
        self.ein = self.einschalten.EinschaltenScraper()

    @staticmethod
    def movie_card(url="/stream/example--abc", title="Example &amp; Friends", flag="ger"):
        return '''<table><tr><td><img src="/posters/example.jpg"></td><td>
            <h2><a href="%s">%s</a><img src="/img/%s_flag.png"></h2>
            <table id="tablemoviesindex"><tr><td><a href="%s">Ansehen auf VOE</a></td></tr></table>
            </td></tr></table>''' % (url, title, flag, url)

    @staticmethod
    def mirror(url, quality="HD"):
        return '''<div id="tablemoviesindex2"><a href="%s" onclick="return loadMirror('%s')">
            <span>28-08-25 13:43</span> VOE <img alt="%s"></a></div>''' % (url, url, quality)

    def test_movie2k_search_reads_cards_without_duplicate_host_rows(self):
        self.m2k.fetch_text = Mock(return_value='<div id="maincontentnew">' + self.movie_card() + '</div>')
        results = self.m2k.search(" Example & Friends ")
        self.assertEqual(results, [{"title": "Example & Friends", "year": None,
                                   "url": "https://movie2k.cx/stream/example--abc",
                                   "poster": "https://movie2k.cx/posters/example.jpg", "provider": "movie2k"}])
        self.assertEqual(self.m2k.fetch_text.call_args.args[0],
                         "https://movie2k.cx/search?q=Example+%26+Friends")
        self.assertEqual(self.m2k.fetch_text.call_args.kwargs["max_bytes"], 1024 * 1024)

    def test_movie2k_skips_series_external_english_and_duplicate_cards(self):
        cards = [self.movie_card(), self.movie_card(), self.movie_card("/series/example"),
                 self.movie_card("https://outside.invalid/stream/example"),
                 self.movie_card("/stream/english", flag="eng")]
        self.m2k.fetch_text = Mock(return_value='<div id="maincontentnew">' + ''.join(cards) + '</div>')
        self.assertEqual(len(self.m2k.search("Example")), 1)

    def test_movie2k_search_limits_results_and_ignores_unrelated_html(self):
        cards = ''.join(self.movie_card("/stream/movie-%d" % i) for i in range(30))
        self.m2k.fetch_text = Mock(return_value='<div id="maincontentnew">' + cards + '</div>')
        self.assertEqual(len(self.m2k.search("Example")), 20)
        self.m2k.fetch_text.return_value = self.movie_card()
        self.assertEqual(self.m2k.search("Example"), [])

    def test_movie2k_sources_only_explicit_mirrors_and_preserve_headers_quality(self):
        mirror = self.mirror("https://voe.sx/e/example?x=1&amp;y=2", "1080p")
        self.m2k.fetch_text = Mock(return_value=mirror + mirror +
            self.mirror("javascript:alert(1)") + self.mirror("https://youtube.com/embed/trailer") +
            '<a href="https://ads.invalid/" onclick="return loadMirror(\'ad\')">ad</a>' +
            '<div id="tablemoviesindex2"><a href="https://ads.invalid/">ad</a></div>')
        sources = self.m2k.get_sources({"url": "https://movie2k.cx/stream/example--abc"})
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["url"], "https://voe.sx/e/example?x=1&y=2")
        self.assertEqual(sources[0]["quality"], "1080p")
        self.assertEqual(sources[0]["language"], "DE")
        self.assertEqual(sources[0]["referer"], "https://movie2k.cx/stream/example--abc")
        self.assertEqual(sources[0]["resolver"], "embed")
        self.assertEqual(sources[0]["provider"], "movie2k")

    def test_movie2k_sources_limit_and_invalid_catalog_urls(self):
        self.m2k.fetch_text = Mock(return_value=''.join(
            self.mirror("https://host.invalid/e/%d" % i) for i in range(30)))
        self.assertEqual(len(self.m2k.get_sources({"url": "/stream/example--abc"})), 16)
        self.m2k.fetch_text.reset_mock()
        for url in ("https://outside.invalid/stream/example", "/series/example", "", "/stream/"):
            self.assertEqual(self.m2k.get_sources({"url": url}), [])
        self.m2k.fetch_text.assert_not_called()

    def test_einschalten_search_matches_public_api_contract(self):
        self.ein.fetch_json = Mock(return_value={"data": [{"id": 27205, "title": "Inception",
            "releaseDate": "2010-07-15", "posterPath": "/poster.jpg"}], "pagination": {"hasMore": False}})
        result = self.ein.search(" Inception & Friends ")
        self.assertEqual(result, [{"title": "Inception", "year": 2010, "id": "27205",
            "url": "https://einschalten.in/movies/27205", "poster": "https://image.tmdb.org/t/p/w500/poster.jpg",
            "provider": "einschalten"}])
        call = self.ein.fetch_json.call_args
        self.assertEqual(call.args[0], "https://einschalten.in/api/search")
        self.assertEqual(call.kwargs["method"], "POST")
        self.assertEqual(json.loads(call.kwargs["data"]), {"query": "Inception & Friends", "pageNumber": 1})
        self.assertEqual(call.kwargs["headers"]["Content-Type"], "application/json")
        self.assertEqual(call.kwargs["max_bytes"], 256 * 1024)

    def test_einschalten_search_rejects_malformed_rows_and_deduplicates(self):
        good = {"id": 1, "title": "Film", "releaseDate": None, "posterPath": "//outside.invalid/p.jpg"}
        rows = [None, [], {}, {"id": True, "title": "Invalid"}, {"id": "../5", "title": "Invalid"},
                {"id": 3, "title": []}, good, good, {"id": 4, "title": " "}]
        self.ein.fetch_json = Mock(return_value={"data": rows})
        result = self.ein.search("Film")
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0]["year"])
        self.assertIsNone(result[0]["poster"])
        self.ein.fetch_json.return_value = {"data": [{"id": i, "title": "Film"} for i in range(1, 30)]}
        self.assertEqual(len(self.ein.search("Film")), 20)

    def test_einschalten_uses_public_player_response_not_metadata_homepage(self):
        self.ein.fetch_json = Mock(return_value={"releaseName": "Example.2010.German.1080p.WEB.x264",
            "streamUrl": "https://vide0.net/e/example", "homepage": "https://ads.invalid/"})
        result = self.ein.get_sources({"url": "https://einschalten.in/movies/27205"})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["url"], "https://vide0.net/e/example")
        self.assertEqual(result[0]["quality"], "1080p")
        self.assertEqual(result[0]["provider"], "einschalten")
        self.assertEqual(result[0]["referer"], "https://einschalten.in/movies/27205")
        self.assertEqual(result[0]["resolver"], "embed")
        call = self.ein.fetch_json.call_args
        self.assertEqual(call.args[0], "https://einschalten.in/api/movies/27205/watch")
        self.assertEqual(call.kwargs["headers"]["Referer"], "https://einschalten.in/movies/27205")
        self.assertEqual(call.kwargs["max_bytes"], 64 * 1024)

    def test_einschalten_rejects_unrelated_urls_before_fetch(self):
        self.ein.fetch_json = Mock()
        for url in ("https://outside.invalid/movies/1", "/movies/../1", "/movies/-1",
                    "/movies/0", "/movies/1/watch", ""):
            self.assertEqual(self.ein.get_sources({"url": url}), [])
        self.ein.fetch_json.assert_not_called()

    def test_einschalten_empty_error_and_non_http_sources(self):
        self.ein.fetch_json = Mock()
        for data in (None, [], {"error": "Unavailable"}, {"streamUrl": "javascript:bad()"},
                     {"streamUrl": "https://user:pass@host.invalid/file"}):
            self.ein.fetch_json.return_value = data
            self.assertEqual(self.ein.get_sources({"url": "/movies/27205"}), [])
        for data in (None, [], {"data": "error"}, {"error": "Unavailable"}):
            self.ein.fetch_json.return_value = data
            self.assertEqual(self.ein.search("Film"), [])

    def test_unsupported_media_and_empty_queries_do_not_fetch(self):
        for provider in (self.m2k, self.ein):
            provider.fetch_text = Mock()
            provider.fetch_json = Mock()
            self.assertEqual(provider.search("Example", "tvshow"), [])
            self.assertEqual(provider.search("  "), [])
            provider.fetch_text.assert_not_called()
            provider.fetch_json.assert_not_called()


if __name__ == "__main__":
    unittest.main()
