"""Bounded host metadata and live-check regression fixtures, without network."""
import importlib.util
from pathlib import Path
import sys
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

from test_dood import load_resolvers, page

SOURCE = Path(__file__).resolve().parents[1] / 'src/plugin.video.streamhub/resources/streamhub/hosters.py'


class ResolverLimitTests(unittest.TestCase):
    def setUp(self):
        self.module = load_resolvers()
        self.request = self.module['request']

    def test_native_metadata_downloads_are_capped(self):
        self.request.return_value = page('https://host.invalid/e/AbC123', '', 404)
        cases = ('resolve_streamtape', 'resolve_mixdrop', 'resolve_packed',
                 'resolve_vidara', 'resolve_filemoon')
        for name in cases:
            with self.subTest(resolver=name):
                self.request.reset_mock()
                self.module[name]('https://host.invalid/e/AbC123')
                self.assertGreater(self.request.call_count, 0)
                self.assertTrue(all(call.kwargs.get('max_bytes') == 1024 * 1024
                                    for call in self.request.call_args_list))

    def test_packed_without_referer_does_not_repeat_identical_request(self):
        self.request.return_value = page('https://host.invalid/e/AbC123', '')
        self.assertIsNone(self.module['resolve_packed']('https://host.invalid/e/AbC123'))
        self.request.assert_called_once()

    def test_packed_keeps_distinct_referer_fallback(self):
        self.request.return_value = page('https://host.invalid/e/AbC123', '')
        self.module['resolve_packed']('https://host.invalid/e/AbC123', 'https://provider.invalid/')
        self.assertEqual(self.request.call_count, 2)
        self.assertEqual(self.request.call_args.kwargs['headers']['Referer'], 'https://provider.invalid/')

    def test_filemoon_does_not_repeat_current_origin(self):
        self.request.return_value = page('https://filemoon.to/e/AbC123', '', 404)
        self.assertIsNone(self.module['resolve_filemoon']('https://filemoon.to/e/AbC123'))
        urls = [call.args[1] for call in self.request.call_args_list]
        self.assertEqual(len(urls), 2)
        self.assertEqual(len(set(urls)), 2)


    def test_normal_packed_player_source_is_preserved(self):
        packed = "}('0.1=\"2\";',3,3,'MDCore|wurl|https://cdn.invalid/video.mp4'.split('|')"
        self.assertEqual(self.module['unpack'](packed), 'MDCore.wurl="https://cdn.invalid/video.mp4";')

    def test_malformed_packed_counts_bases_and_large_inputs_are_rejected(self):
        for base, count in ((1, 10), (63, 10), (2, 99999999999999999)):
            with self.subTest(base=base, count=count):
                packed = "}('0',%d,%d,'text'.split('|')" % (base, count)
                self.assertEqual(self.module['unpack'](packed), '')
        self.assertEqual(self.module['unpack']('x' * 262145), '')

    def test_packed_expansion_cannot_allocate_unbounded_result(self):
        packed = "}('%s',2,1,'%s'.split('|')" % ('0 ' * 10000, 'a' * 1000)
        self.assertEqual(self.module['unpack'](packed), '')


class LiveCheckLimitTests(unittest.TestCase):
    def setUp(self):
        utils = types.ModuleType('streamhub.utils')
        for name, value in dict(request=Mock(), log=Mock(), urlparse=urlparse,
                                ThreadPoolExecutor=ThreadPoolExecutor, as_completed=as_completed,
                                format_exc=lambda: 'fixture', stats_line=lambda **kw: '').items():
            setattr(utils, name, value)
        with patch.dict(sys.modules, {'streamhub.utils': utils}):
            spec = importlib.util.spec_from_file_location('limited_hosters', SOURCE)
            self.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.module)

    def test_deadline_does_not_wait_for_workers_or_start_queued_checks(self):
        release, finished = threading.Event(), threading.Event()
        calls = []
        def check(url, timeout):
            calls.append(url)
            try:
                release.wait(2)
                return True
            finally:
                finished.set()
        self.module._check_one = check
        candidates = [{'url': 'https://host.invalid/' + str(i)} for i in range(20)]
        started = time.monotonic()
        try:
            self.assertEqual(self.module.filter_alive(candidates, max_workers=1, deadline_s=0.03), [])
            self.assertLess(time.monotonic() - started, 0.4)
            self.assertLessEqual(len(calls), 1)
        finally:
            release.set()
            finished.wait(1)
        self.assertTrue(all('_alive' not in candidate for candidate in candidates))

    def test_pre_cancelled_check_makes_no_requests(self):
        self.module._check_one = Mock()
        progress = Mock()
        progress.iscanceled.return_value = True
        self.assertEqual(self.module.filter_alive([{'url': 'https://host.invalid/a'}], prog=progress), [])
        self.module._check_one.assert_not_called()

    def test_results_stay_in_original_order_and_failed_checks_are_excluded(self):
        self.module._check_one = lambda url, timeout: not url.endswith('bad')
        candidates = [{'url': 'https://host.invalid/' + path} for path in ('first', 'bad', 'last')]
        self.assertEqual(self.module.filter_alive(candidates), [candidates[0], candidates[2]])
        self.assertIs(candidates[1]['_alive'], False)


if __name__ == '__main__':
    unittest.main()
