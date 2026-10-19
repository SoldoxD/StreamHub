"""Bounded metadata fetches, retry timing and cancellation regressions."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[1] / "src/plugin.video.streamhub/resources/lib/scrapers/base.py"


class ScraperBaseTests(unittest.TestCase):
    def setUp(self):
        cache = tempfile.TemporaryDirectory()
        self.addCleanup(cache.cleanup)
        utils = types.ModuleType("streamhub.utils")
        utils.request = Mock()
        utils.log = Mock()
        utils.cachepath = cache.name
        utils.format_exc = lambda: "fixture error"
        with patch.dict(sys.modules, {"streamhub.utils": utils}):
            spec = importlib.util.spec_from_file_location("scraper_base_audit", SOURCE)
            self.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.module)
        self.scraper = self.module.BaseScraper(token=self.module.CancelToken())
        self.request = utils.request
        self.utils = utils
        self.utils.request_json = Mock(return_value=[])
        self.utils.getAuthSignature = Mock(return_value="test")

    def load_provider(self, name):
        package = types.ModuleType("lib.scrapers")
        package.register = lambda cls: cls
        with patch.dict(sys.modules, {"lib": types.ModuleType("lib"),
                                     "lib.scrapers": package,
                                     "lib.scrapers.base": self.module,
                                     "streamhub.utils": self.utils}):
            spec = importlib.util.spec_from_file_location("audit_" + name, SOURCE.parent / (name + ".py"))
            provider = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(provider)
        return provider

    def test_specials_cache_key_is_distinct_from_movie(self):
        for scraper in (self.scraper,
                        self.load_provider("movies67").Movies67Scraper(),
                        self.load_provider("vavoo").VavooScraper()):
            with self.subTest(provider=scraper.name):
                item = {"id": "test", "tmdb_id": "42", "episode": 1}
                self.assertNotEqual(scraper.source_key(item),
                                    scraper.source_key(dict(item, season=0)))

    def test_67movies_specials_use_tv_route_and_missing_season_uses_movie(self):
        scraper = self.load_provider("movies67").Movies67Scraper()
        specials = scraper.get_sources({"tmdb_id": "42", "season": 0, "episode": 1})
        self.assertTrue(all("/tv/42/0/1" in source["url"] for source in specials))
        for season in (None, ""):
            movie = scraper.get_sources({"tmdb_id": "42", "season": season})
            self.assertTrue(all("/movie/42" in source["url"] for source in movie))

    def test_vavoo_specials_preserve_zero_in_series_payload(self):
        scraper = self.load_provider("vavoo").VavooScraper()
        scraper.get_sources({"tmdb_id": "42", "season": 0, "episode": 1})
        payload = self.utils.request_json.call_args.kwargs["json"]
        self.assertEqual(payload["type"], "series")
        self.assertEqual(payload["episode"], {"season": "0", "episode": "1"})
        for season in (None, ""):
            scraper.get_sources({"tmdb_id": "42", "season": season})
            payload = self.utils.request_json.call_args.kwargs["json"]
            self.assertEqual(payload["type"], "movie")
            self.assertNotIn("episode", payload)

    def test_retry_wait_occurs_only_before_another_attempt(self):
        self.request.side_effect = OSError("connection failed")
        self.scraper._wait = Mock()
        self.assertIsNone(self.scraper.fetch("https://provider.invalid/page"))
        self.assertEqual(self.request.call_count, 3)
        self.assertEqual(self.scraper._wait.call_count, 2)

    def test_single_attempt_failure_returns_without_backoff(self):
        self.scraper.retries = 1
        self.request.side_effect = OSError("connection failed")
        self.scraper._wait = Mock()
        self.assertIsNone(self.scraper.fetch("https://provider.invalid/page"))
        self.scraper._wait.assert_not_called()

    def test_metadata_has_default_limit_and_explicit_zero_is_preserved(self):
        self.request.return_value = Mock(status_code=200)
        self.scraper.fetch("https://provider.invalid/page")
        self.assertEqual(self.request.call_args.kwargs["max_bytes"], 2 * 1024 * 1024)
        self.scraper.fetch("https://provider.invalid/page", max_bytes=0)
        self.assertEqual(self.request.call_args.kwargs["max_bytes"], 0)

    def test_retry_closes_discarded_response_before_waiting(self):
        limited, success = Mock(status_code=429), Mock(status_code=200)
        self.request.side_effect = [limited, success]
        self.scraper._wait = Mock(side_effect=lambda seconds: limited.close.assert_called_once())
        self.assertIs(self.scraper.fetch("https://provider.invalid/page"), success)
        success.close.assert_not_called()

    def test_cancelled_inflight_response_is_closed_and_not_returned(self):
        response = Mock(status_code=200)
        def cancel(*args, **kwargs):
            self.scraper.token.cancel()
            return response
        self.request.side_effect = cancel
        with self.assertRaises(self.module.Cancelled):
            self.scraper.fetch("https://provider.invalid/page")
        response.close.assert_called_once()
        self.assertEqual(self.request.call_count, 1)

    def test_backoff_is_interrupted_after_first_short_sleep(self):
        clock = [0.0]
        def sleep(seconds):
            clock[0] += seconds
            self.scraper.token.cancel()
        with patch.object(self.module.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(self.module.time, "sleep", side_effect=sleep) as sleeper:
            with self.assertRaises(self.module.Cancelled):
                self.scraper._wait(2.0)
        self.assertEqual(sleeper.call_count, 1)
        self.assertLessEqual(clock[0], 0.1)

    def test_cancelled_lookup_never_reads_cached_results(self):
        self.scraper.token.cancel()
        with patch.object(self.module, "cache_get") as get:
            for lookup, item in ((self.scraper.cached_search, "Film"),
                                 (self.scraper.cached_sources, {"id": "film"})):
                with self.assertRaises(self.module.Cancelled):
                    lookup(item)
            get.assert_not_called()

    def test_cancelled_provider_result_never_poison_caches(self):
        for kind in ("search", "sources"):
            with self.subTest(kind=kind):
                scraper = self.module.BaseScraper(token=self.module.CancelToken())
                def cancelled_result(*args):
                    scraper.token.cancel()
                    return []
                scraper.search = cancelled_result
                scraper.get_sources = cancelled_result
                with patch.object(self.module, "cache_get", return_value=None), \
                        patch.object(self.module, "cache_set") as save:
                    with self.assertRaises(self.module.Cancelled):
                        if kind == "search":
                            scraper.cached_search("Film")
                        else:
                            scraper.cached_sources({"id": "film"})
                    save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
