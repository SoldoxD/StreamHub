"""Offline continuation budgets for relay and live-player startup."""
import ast
import base64
import json
from pathlib import Path
import re
import sys
import types
import unittest
from unittest.mock import Mock, patch
from urllib.parse import quote, parse_qsl, urlparse

SOURCE = Path(__file__).resolve().parents[1] / 'src/plugin.video.streamhub/resources/streamhub'


def load(filename, clock, stopped):
    tree = ast.parse((SOURCE / filename).read_text(encoding='utf-8'))
    tree.body = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
    namespace = dict(base64=base64, json=json, re=re, quote=quote, urlparse=urlparse,
                     time=types.SimpleNamespace(monotonic=lambda: clock[0]),
                     monitor=types.SimpleNamespace(abortRequested=lambda: stopped[0]),
                     request=Mock(), log=Mock(), format_exc=lambda: 'fixture error')
    exec(compile(tree, filename, 'exec'), namespace)
    return namespace


def response(text='', url='https://player.invalid/embed', content=b''):
    return types.SimpleNamespace(text=text, url=url, content=content, headers={},
                                 status_code=200, raise_for_status=Mock(), close=Mock())


class RelayBudgetTests(unittest.TestCase):
    def setUp(self):
        self.clock, self.stopped = [0.0], [False]
        self.module = load('vjackson.py', self.clock, self.stopped)
        self.api = self.module['callApi'] = Mock()
        self.request = self.module['request']
        self.modules = patch.dict(sys.modules, {
            'streamhub.movies': types.SimpleNamespace(_cancelled=lambda: False)})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.fetch = {'id': 'same-session', 'data': {
            'type': 'fetch', 'url': 'https://relay.invalid/meta', 'params': {}}}

    def test_repeated_relay_response_cannot_loop_forever(self):
        self.api.return_value = self.fetch
        self.request.return_value = response()
        self.assertIsNone(self.module['callApi2']('open', {}))
        self.assertEqual(self.request.call_count, self.module['_RELAY_MAX_HOPS'])
        self.assertEqual(self.api.call_count, self.module['_RELAY_MAX_HOPS'] + 1)
        self.assertTrue(all(call.kwargs['retries'] == 0 for call in self.request.call_args_list))

    def test_relay_keeps_payload_headers_redirect_policy_and_terminal_result(self):
        post = {'id': 'post-session', 'data': {'type': 'fetch',
            'url': 'https://relay.invalid/meta', 'params': {
                'method': 'post', 'body': base64.b64encode(b'input').decode(),
                'headers': {'Cookie': ['token=value']}, 'redirect': 'manual'}}}
        final = {'url': 'https://cdn.invalid/video'}
        self.api.side_effect = [post, {'id': 'post-session', 'data': final}]
        fetched = response(content=b'output')
        self.request.return_value = fetched
        self.assertEqual(self.module['callApi2']('open', {}), final)
        call = self.request.call_args
        self.assertEqual(call.args, ('POST', 'https://relay.invalid/meta'))
        self.assertEqual(call.kwargs['headers'], {'Cookie': 'token=value'})
        self.assertEqual(call.kwargs['data'], b'input')
        self.assertFalse(call.kwargs['allow_redirects'])
        self.assertEqual(call.kwargs['max_bytes'], 1024 * 1024)
        self.assertEqual(self.api.call_args.kwargs['json']['data'], base64.b64encode(b'output').decode())
        fetched.close.assert_called_once()

    def test_expired_relay_does_not_submit_another_api_round(self):
        self.api.return_value = self.fetch
        fetched = response()
        def slow_fetch(*args, **kwargs):
            self.clock[0] = 21.0
            return fetched
        self.request.side_effect = slow_fetch
        self.assertIsNone(self.module['callApi2']('open', {}))
        self.api.assert_called_once()
        fetched.close.assert_called_once()

    def test_cancelled_relay_makes_no_requests(self):
        for movie_cancelled, shutdown in ((True, False), (False, True)):
            with self.subTest(movie_cancelled=movie_cancelled, shutdown=shutdown):
                self.stopped[0] = shutdown
                with patch.dict(sys.modules, {'streamhub.movies': types.SimpleNamespace(
                        _cancelled=lambda: movie_cancelled)}):
                    self.assertIsNone(self.module['callApi2']('open', {}))
                self.api.assert_not_called()
                self.request.assert_not_called()

    def test_call_api_forwards_explicit_timeouts_without_duplicate_arguments(self):
        module = load('vjackson.py', self.clock, self.stopped)
        module['getAuthSignature'] = Mock(return_value='signature')
        fetched = response()
        fetched.json = Mock(return_value={'ok': True})
        module['request'].return_value = fetched
        self.assertEqual(module['callApi']('open', {}, timeout=2, retries=0, max_bytes=1024), {'ok': True})
        self.assertEqual(module['request'].call_args.kwargs['timeout'], 2)
        self.assertEqual(module['request'].call_args.kwargs['retries'], 0)


class DlhdBudgetTests(unittest.TestCase):
    def setUp(self):
        self.clock, self.stopped = [0.0], [False]
        self.module = load('dlhd.py', self.clock, self.stopped)
        self.request = self.module['request']

    def test_success_preserves_stream_and_player_origin_headers(self):
        media = 'https://cdn.invalid/live/master.m3u8?token=fixture'
        encoded = base64.b64encode(media.encode()).decode()
        self.request.side_effect = [response('<iframe src="https://player.invalid/e/123" id="thatframe">'),
                                    response("window.atob('%s')" % encoded)]
        stream, headers = self.module['resolve_stream']('dlhd://42')
        self.assertEqual(stream, media)
        self.assertEqual(dict(parse_qsl(headers))['Origin'], 'https://player.invalid')
        self.assertEqual(dict(parse_qsl(headers))['Referer'], 'https://player.invalid/')
        self.assertEqual(self.request.call_count, 2)
        self.assertTrue(all(call.kwargs['retries'] == 0 and call.kwargs['max_bytes'] == 1024 * 1024
                            for call in self.request.call_args_list))

    def test_folder_fallbacks_stop_after_shared_budget_and_shrink_timeout(self):
        def fail(*args, **kwargs):
            self.clock[0] += kwargs['timeout']
            raise OSError('timeout')
        self.request.side_effect = fail
        with self.assertRaises(RuntimeError):
            self.module['resolve_stream']('42')
        self.assertEqual(self.request.call_count, 3)
        self.assertEqual([call.kwargs['timeout'] for call in self.request.call_args_list], [6, 6, 3])
        self.assertEqual(self.clock[0], 15)

    def test_shutdown_prevents_next_iframe_and_folder_requests(self):
        def cancelled(*args, **kwargs):
            self.stopped[0] = True
            return response('<iframe src="https://player.invalid/e/123">')
        self.request.side_effect = cancelled
        with self.assertRaises(RuntimeError):
            self.module['resolve_stream']('42')
        self.request.assert_called_once()

    def test_fast_failed_folder_keeps_working_folder_fallback(self):
        media = 'https://cdn.invalid/live/master.m3u8?token=fixture'
        encoded = base64.b64encode(media.encode()).decode()
        self.request.side_effect = [response('No player here'),
            response('<iframe src="https://player.invalid/e/123">'),
            response("window.atob('%s')" % encoded)]
        self.assertEqual(self.module['resolve_stream']('42')[0], media)
        self.assertIn('/cast/', self.request.call_args_list[1].args[1])


if __name__ == '__main__':
    unittest.main()
