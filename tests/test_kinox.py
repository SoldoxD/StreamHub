"""Kinox public HTML/JSON fixtures; no host requests or challenge execution."""
import json
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch


DIRECTORY = Path(__file__).resolve().parents[1] / "src/plugin.video.streamhub/resources/lib/scrapers"
BASE = "https://kinox.to"
PAGE = BASE + "/Stream/Example.html"


def hoster(number=1, date=True, name="VOE"):
    return ('<li rel="Example&amp;Hoster=%s" class="MirBtn" id="Hoster_%s">'
            '<div class="Named"><b>%s</b></div><div class="Data">Mirror: 1/1%s</div></li>') % (
                number, number, name, '<br><b>Vom</b>: 14.12.2025' if date else '')


def response(code=302, location=None, text=""):
    return types.SimpleNamespace(status_code=code, headers={"Location": location} if location else {}, text=text)


class KinoxTests(unittest.TestCase):
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
        names = ("base", "html_catalog", "kinox")
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
        self.scraper = self.kinox.KinoxScraper()


    def test_search_attribute_order_quotes_entities_and_scope(self):
        self.scraper.fetch_text = Mock(return_value="""<a href='/Stream/Sidebar.html'>Sidebar</a>
            <ul id='content_list'>
            <a href='/Stream/Example.html' title='Example &amp; Friends'>Title</a>
            <a title='Duplicate' href='/Stream/Example.html'>Duplicate</a>
            <a title='Other' href='/Stream/Other.html'>Other</a>
            <a href='//kinox.to/Stream/Third.html'>Third</a>
            <a href='https://kinox.to.evil/Stream/Fake.html'>Fake</a>
            <a href='/Stream/Show.html' title='Show Staffel 2'>Show</a>
            <a href='/other'>Other page</a></ul>""")
        result = self.scraper.search('Example & Friends')
        self.assertEqual([item['title'] for item in result], ['Example & Friends', 'Other', 'Third'])
        self.assertEqual(result[0]['url'], PAGE)
        call = self.scraper.fetch_text.call_args
        self.assertIn('q=Example%20%26%20Friends', call.args[0])
        self.assertEqual(call.kwargs['max_bytes'], 1024 * 1024)
        self.assertFalse(call.kwargs['allow_redirects'])

    def test_search_limits_results_and_rejects_unsupported_media(self):
        self.scraper.fetch_text = Mock(return_value='<ul id="content_list">' + ''.join(
            '<a href="/Stream/Movie%s.html">Movie %s</a>' % (n, n) for n in range(30)) + '</ul>')
        self.assertEqual(len(self.scraper.search('Movie')), 20)
        self.scraper.fetch_text.reset_mock()
        self.assertEqual(self.scraper.search('Movie', 'tvshow'), [])
        self.assertEqual(self.scraper.search('  '), [])
        self.scraper.fetch_text.assert_not_called()

    def test_search_without_result_list_does_not_return_sidebar_links(self):
        self.scraper.fetch_text = Mock(return_value='<a href="/Stream/Sidebar.html">Sidebar</a>')
        self.assertEqual(self.scraper.search('Example'), [])

    def test_hosters_handle_attribute_order_nested_names_and_missing_date(self):
        self.scraper.fetch_text = Mock(return_value=hoster(99) + '<ul id="HosterList">' +
            hoster(1) + hoster(2, False, 'Dood.to') + hoster(1) +
            hoster(3).replace('Example&amp;', 'Unrelated&amp;') + '</ul>')
        rows = self.scraper._hosters(PAGE)
        self.assertEqual([row['rel'] for row in rows], ['Example&Hoster=1', 'Example&Hoster=2'])
        self.assertEqual([row['date'] for row in rows], ['14.12.2025', None])
        self.assertEqual(rows[1]['name'], 'Dood.to')

    def test_hosters_are_limited_and_malformed_rel_is_ignored(self):
        self.scraper.fetch_text = Mock(return_value='<ul id="HosterList">' +
            hoster(99).replace('Example&amp;', '../Example&amp;') +
            ''.join(hoster(n) for n in range(25)) + '</ul>')
        self.assertEqual(len(self.scraper._hosters(PAGE)), 12)

    def test_live_mirror_json_double_quote_and_http_location(self):
        # The live provider emits an extra quote immediately after src.
        self.scraper.fetch_text = Mock(return_value=json.dumps({'Stream':
            '<iframe src="/redirect/7a97ddb3a2e3caedeb5b60b8f581e551?t=1792437451"" '
            'referrerpolicy="no-referrer" allowfullscreen="true"></iframe>',
            'Replacement': False, 'HosterName': 'Vinovo.to', 'HosterHome': 'http://vinovo.to/'}))
        self.scraper.fetch = Mock(return_value=response(302, '//vinovo.to/e/example'))
        target = self.scraper._mirror_target('Example&Hoster=104', PAGE)
        self.assertEqual(target, 'https://vinovo.to/e/example')
        self.scraper.fetch.assert_called_once()
        call = self.scraper.fetch.call_args
        self.assertFalse(call.kwargs['allow_redirects'])
        self.assertEqual(call.kwargs['max_bytes'], 64 * 1024)
        self.assertTrue(call.args[0].startswith(BASE + '/redirect/'))
        mirror = self.scraper.fetch_text.call_args
        self.assertEqual(mirror.kwargs['max_bytes'], 64 * 1024)
        self.assertEqual(mirror.kwargs['headers']['X-Requested-With'], 'XMLHttpRequest')

    def test_direct_iframe_and_anchor_in_stream_do_not_fetch_host(self):
        for stream in ("<iframe allowfullscreen src='//voe.sx/e/example?x=1&amp;y=2'></iframe>",
                       '<a title="Watch" href="https://voe.sx/e/example?x=1&amp;y=2">Watch</a>'):
            self.scraper.fetch_text = Mock(return_value=json.dumps({'Stream': stream}))
            self.scraper.fetch = Mock(side_effect=AssertionError('must not fetch host'))
            self.assertEqual(self.scraper._mirror_target('Example&Hoster=1', PAGE),
                             'https://voe.sx/e/example?x=1&y=2')
            self.scraper.fetch.assert_not_called()

    def test_redirect_page_parses_real_attributes_and_skips_assets_ads(self):
        html = """<script src='https://cdn.invalid/script.js'></script>
            <img src='https://cdn.invalid/image.jpg'>
            <iframe src='https://www.facebook.com/plugins/like.php'></iframe>
            <a class='advertisement' href='https://unrelated.invalid/offer'>Ad</a>
            <iframe class='player' src='//voe.sx/e/example'></iframe>"""
        self.scraper.fetch = Mock(return_value=response(200, text=html))
        self.assertEqual(self.scraper._follow_redirect(BASE + '/redirect/example', PAGE),
                         'https://voe.sx/e/example')
        self.scraper.fetch.assert_called_once()

    def test_redirect_anchor_and_entities_are_supported(self):
        self.scraper.fetch = Mock(return_value=response(200, text=
            "<a href='//dropload.io/e/example?x=1&amp;y=2'>Watch</a>"))
        self.assertEqual(self.scraper._follow_redirect(BASE + '/redirect/example', PAGE),
                         'https://dropload.io/e/example?x=1&y=2')

    def test_live_verification_page_fails_closed_without_reload(self):
        self.scraper.fetch = Mock(return_value=response(200, text="""
            <!DOCTYPE html><html><head><title>Verifizierung</title>
            <script>(function(){var key='abc', data='000000';
            (new Function('setTimeout(function(){window.location.reload();},1000)'))();})();</script>
            </head><body>Please wait, checking your request...
            <a href='https://advert.invalid/offer'>Continue</a></body></html>"""))
        self.assertEqual(self.scraper._follow_redirect(BASE + '/redirect/example', PAGE), '')
        self.scraper.fetch.assert_called_once()

    def test_redirects_have_cycle_and_hop_limits(self):
        self.scraper.fetch = Mock(return_value=response(302, '/redirect/example'))
        self.assertEqual(self.scraper._follow_redirect(BASE + '/redirect/example', PAGE), '')
        self.scraper.fetch.assert_called_once()
        self.scraper.fetch = Mock(side_effect=[response(302, '/redirect/two'),
            response(302, '/redirect/three'), response(302, '/redirect/four')])
        self.assertEqual(self.scraper._follow_redirect(BASE + '/redirect/one', PAGE), '')
        self.assertEqual(self.scraper.fetch.call_count, 3)

    def test_unsafe_login_and_error_redirects_are_not_sources(self):
        for res in (None, response(403), response(302, '/login'),
                    response(302, 'javascript:bad()'), response(302, 'https://user:pass@host.invalid/e/id'),
                    response(302, 'https://ads.invalid/ad'), response(200, text='<script>unrelated()</script>')):
            self.scraper.fetch = Mock(return_value=res)
            self.assertEqual(self.scraper._follow_redirect(BASE + '/redirect/example', PAGE), '')

    def test_invalid_json_and_stream_types_are_safe(self):
        self.scraper.fetch = Mock(side_effect=AssertionError('must not fetch'))
        for body in ('invalid', '[]', 'null', '{"Stream": 1}', '{"Stream": null}', '{}'):
            self.scraper.fetch_text = Mock(return_value=body)
            self.assertEqual(self.scraper._mirror_target('Example&Hoster=1', PAGE), '')
        self.scraper.fetch_text.reset_mock()
        self.assertEqual(self.scraper._mirror_target('../Example&Hoster=1', PAGE), '')
        self.scraper.fetch_text.assert_not_called()

    def test_sources_keep_host_labels_date_referer_and_deduplicate(self):
        self.scraper.fetch_text = Mock(side_effect=lambda url, **kw:
            '<ul id="HosterList">' + hoster(1) + hoster(2) + '</ul>' if '/Stream/' in url else
            json.dumps({'Stream': '<iframe src="https://voe.sx/e/example"></iframe>'}))
        self.scraper.fetch = Mock(side_effect=AssertionError('must not fetch host'))
        result = self.scraper.get_sources({'url': PAGE})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['host'], 'voe.sx')
        self.assertEqual(result[0]['label'], 'VOE (kinox)')
        self.assertEqual(result[0]['referer'], PAGE)
        self.assertEqual(result[0]['added'], '14.12.2025')

    def test_invalid_provider_urls_do_not_fetch(self):
        self.scraper.fetch_text = Mock()
        for item in ({}, {'url': 'https://kinox.to.evil/Stream/Example.html'}, {'url': BASE + '/login'}):
            self.assertEqual(self.scraper.get_sources(item), [])
        self.scraper.fetch_text.assert_not_called()

    def test_cancellation_stops_before_requests(self):
        self.scraper.token = self.base.CancelToken()
        self.scraper.token.cancel()
        self.scraper.fetch = Mock()
        with self.assertRaises(self.base.Cancelled):
            self.scraper._follow_redirect(BASE + '/redirect/example', PAGE)
        self.scraper.fetch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
