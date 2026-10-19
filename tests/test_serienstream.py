"""Public SerienStream markup fixtures; no network, account or Kodi needed."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch


DIRECTORY = Path(__file__).resolve().parents[1] / "src/plugin.video.streamhub/resources/lib/scrapers"
BASE = "https://serienstream.to"
SERIES = BASE + "/serie/example"


def card(path="/serie/example", title="Example &amp; Friends"):
    return ('<div class="cover-card"><a class="show-cover" href="%s">'
            '<img src="/poster.jpg"></a><h6 class="show-title">%s</h6></div>') % (path, title)


def episode(number, season=1, path="example", title="Episode title"):
    return ('<tr class="episode-row" onclick="window.location=\'/serie/%s/staffel-%s/episode-%s\'">'
            '<strong class="episode-title-ger">%s</strong>'
            '<span class="episode-title-eng">English title</span></tr>') % (path, season, number, title)


def button(token="one", language="1", provider="VOE"):
    return ('<button class="link-box" data-play-url="/r?t=%s" '
            'data-language-id="%s" data-provider-name="%s">%s</button>') % (token, language, provider, provider)


def response(code=302, location=None):
    return types.SimpleNamespace(status_code=code, headers={"Location": location} if location else {})


class SerienStreamTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        utils = types.ModuleType("streamhub.utils")
        utils.request = Mock(side_effect=AssertionError("unexpected network"))
        utils.log = Mock()
        utils.cachepath = temp.name
        utils.format_exc = lambda: "test traceback"
        package = types.ModuleType("lib.scrapers")
        package.__path__ = [str(DIRECTORY)]
        package.register = lambda cls: cls
        modules = {"streamhub.utils": utils, "lib": types.ModuleType("lib"), "lib.scrapers": package}
        names = ("base", "html_catalog", "serienstream")
        modules.update({"lib.scrapers." + name: None for name in names})
        replacement = patch.dict(sys.modules, modules)
        replacement.start()
        self.addCleanup(replacement.stop)
        for name in names:
            spec = importlib.util.spec_from_file_location("lib.scrapers." + name, DIRECTORY / (name + ".py"))
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            setattr(self, name, module)
        self.scraper = self.serienstream.SerienStreamScraper()

    def test_search_only_series_cards_with_entities_and_encoded_query(self):
        self.scraper.fetch_text = Mock(return_value=card("/serie/sidebar", "Sidebar") +
            '<div class="search-results">' + card() + card() +
            card("https://advert.invalid/serie/ad", "Ad") +
            card("/serie/example/staffel-1/episode-1", "Episode") +
            '<a href="/serie/unrelated">Matching episode, not series</a></div>')
        result = self.scraper.search("Example & Friends", "tvshow")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "Example & Friends")
        self.assertEqual(result[0]["url"], SERIES)
        self.assertEqual(result[0]["poster"], BASE + "/poster.jpg")
        call = self.scraper.fetch_text.call_args
        self.assertIn("term=Example+%26+Friends", call.args[0])
        self.assertEqual(call.kwargs["max_bytes"], 1024 * 1024)

    def test_unsupported_media_and_empty_query_do_not_fetch(self):
        self.scraper.fetch_text = Mock()
        self.assertEqual(self.scraper.search("Example", "movie"), [])
        self.assertEqual(self.scraper.search(" ", "tvshow"), [])
        self.scraper.fetch_text.assert_not_called()

    def test_details_preserve_specials_and_only_observed_seasons(self):
        self.scraper.fetch_text = Mock(return_value='''<h1>Example</h1>
            <span class="description-text">Plot &amp; more</span><nav id="season-nav">
            <a href="/serie/example/staffel-0">Filme</a>
            <a href="/serie/example/staffel-2">2</a>
            <a href="/serie/example/staffel-1">1</a>
            <a href="/serie/example/staffel-1">Duplicate</a>
            <a href="/serie/unrelated/staffel-9">Other show</a></nav>''' + episode(1) + episode(3))
        result = self.scraper.get_details({"url": SERIES})
        self.assertEqual([s["season_number"] for s in result["seasons"]], [0, 1, 2])
        self.assertEqual([s["episode_count"] for s in result["seasons"]], [None, 2, None])
        self.assertEqual(result["season_ids"]["0"], SERIES + "/staffel-0")
        self.assertEqual(result["overview"], "Plot & more")

    def test_episode_list_uses_real_numbers_titles_and_same_series(self):
        self.scraper.fetch_text = Mock(return_value=episode(3) + episode(1, title="") + episode(3) +
                                      episode(2, season=2) + episode(8, path="unrelated") + episode(0))
        result = self.scraper.get_episodes({"url": SERIES}, 1)
        self.assertEqual([e["episode_number"] for e in result], [1, 3])
        self.assertEqual(result[0]["name"], "English title")
        self.assertEqual(result[1]["url"], SERIES + "/staffel-1/episode-3")

    def test_episode_zero_season_and_literal_link_are_supported(self):
        self.scraper.fetch_text = Mock(return_value='<tr class="episode-row">'
            '<a href="/serie/example/staffel-0/episode-1">1</a>'
            '<strong class="episode-title-ger">Special</strong></tr>')
        result = self.scraper.get_episodes({"url": SERIES}, 0)
        self.assertEqual(result[0]["season_number"], 0)
        self.assertEqual(self.scraper.fetch_text.call_args.args[0], SERIES + "/staffel-0")

    def test_invalid_urls_numbers_and_executable_attributes_are_not_used(self):
        self.scraper.fetch_text = Mock(return_value='<tr class="episode-row" '
            'onclick="doSomething(); window.location=\'/serie/example/staffel-1/episode-1\'">Bad</tr>')
        self.assertEqual(self.scraper.get_episodes({"url": SERIES}, 1), [])
        self.scraper.fetch_text.reset_mock()
        for value in (None, -1, 1.5, True, "1/../2", "1000000"):
            self.assertEqual(self.scraper.get_episodes({"url": SERIES}, value), [])
        self.assertEqual(self.scraper.get_details({"url": "https://serienstream.to.evil/serie/example"}), {})
        self.assertEqual(self.scraper.get_sources({"url": SERIES, "season": 1, "episode": 0}), [])
        self.scraper.fetch_text.assert_not_called()

    def test_sources_follow_only_provider_redirects_and_keep_language_referer(self):
        self.scraper.fetch_text = Mock(return_value=button() + button("two", "2") + button() +
            '<a href="https://ads.invalid/">Advertising</a>')
        self.scraper.fetch = Mock(side_effect=lambda url, **kw: response(302, "https://voe.sx/e/" + url[-3:]))
        result = self.scraper.get_sources({"url": SERIES, "season": 1, "episode": 2})
        self.assertEqual(len(result), 2)
        self.assertEqual([source["language"] for source in result], ["DE", "EN"])
        self.assertTrue(all(source["host"] == "voe.sx" for source in result))
        self.assertTrue(all(source["referer"] == SERIES + "/staffel-1/episode-2" for source in result))
        self.assertEqual(self.scraper.fetch.call_count, 2)
        for call in self.scraper.fetch.call_args_list:
            self.assertTrue(call.args[0].startswith(BASE + "/r?"))
            self.assertFalse(call.kwargs["allow_redirects"])
            self.assertEqual(call.kwargs["max_bytes"], 64 * 1024)

    def test_login_challenge_error_and_unsafe_redirect_do_not_become_sources(self):
        self.scraper.fetch_text = Mock(return_value=button())
        for res in (None, response(200), response(403), response(302, "/login"),
                    response(302, "javascript:bad()"), response(302, "https://user:pass@evil.invalid/e/a")):
            self.scraper.fetch = Mock(return_value=res)
            self.assertEqual(self.scraper.get_sources({"url": SERIES, "season": 1, "episode": 1}), [])

    def test_redirect_cycle_and_chain_have_finite_request_limit(self):
        self.scraper.fetch = Mock(return_value=response(302, "/r?t=one"))
        self.assertEqual(self.scraper._host_redirect(BASE + "/r?t=one", SERIES), "")
        self.assertEqual(self.scraper.fetch.call_count, 1)
        self.scraper.fetch = Mock(side_effect=[response(302, "/r?t=two"), response(302, "/r?t=three"),
                                              response(302, "/r?t=four")])
        self.assertEqual(self.scraper._host_redirect(BASE + "/r?t=one", SERIES), "")
        self.assertEqual(self.scraper.fetch.call_count, 3)

    def test_source_links_are_deduplicated_per_language_and_limited(self):
        self.scraper.fetch_text = Mock(return_value="".join(button(str(n)) for n in range(30)))
        self.scraper.fetch = Mock(return_value=response(302, "https://voe.sx/e/one"))
        result = self.scraper.get_sources({"url": SERIES, "season": 0, "episode": 1})
        self.assertEqual(len(result), 1)
        self.assertEqual(self.scraper.fetch.call_count, 12)


if __name__ == "__main__":
    unittest.main()
