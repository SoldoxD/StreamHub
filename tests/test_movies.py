"""Offline regression tests for the real movie selection/playback integration."""
import ast
import importlib.util
import json
import re
import sys
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qsl, quote, urlencode, urlparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

SOURCE = Path(__file__).resolve().parents[1] / 'src/plugin.video.streamhub/resources/streamhub'


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SOURCE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MovieTests(unittest.TestCase):
    def setUp(self):
        self.settings = {'stream_quali': '1', 'pref_lang': 'de'}
        utils = types.ModuleType('streamhub.utils')
        for name, value in dict(re=re, json=json, time=time, parse_qsl=parse_qsl,
                                quote=quote, urlencode=urlencode, urlparse=urlparse,
                                getSetting=lambda key: self.settings.get(key, ''),
                                ThreadPoolExecutor=ThreadPoolExecutor, wait=wait,
                                FIRST_COMPLETED=FIRST_COMPLETED, log=Mock()).items():
            setattr(utils, name, value)
        kodi = types.ModuleType('xbmc')
        kodi.getCondVisibility = lambda _: True
        kodi.getInfoLabel = lambda _: '21.3'
        self.modules = patch.dict(sys.modules, {
            'streamhub': types.ModuleType('streamhub'),
            'streamhub.utils': utils, 'xbmc': kodi,
            'streamhub.resume': types.SimpleNamespace(apply_resume=Mock(), announce_playing=Mock()),
        })
        self.modules.start()
        self.addCleanup(self.modules.stop)
        sys.modules['streamhub.source_selection'] = load('streamhub.source_selection', 'source_selection.py')
        sys.modules['streamhub.language'] = load('streamhub.language', 'language.py')
        self.playback = load('streamhub.playback', 'playback.py')
        sys.modules['streamhub.playback'] = self.playback
        self.movies = load('movie_regressions', 'movies.py')

    def test_language_ranking_does_not_confuse_subtitles_with_audio(self):
        self.assertEqual(self.movies._lang_rank({'lang': 'DE-SUB'}), 2)
        self.assertEqual(self.movies._lang_rank({'lang': 'GER'}), 0)
        self.assertEqual(self.movies._lang_rank({'lang': 'EN / DE-SUB'}), 1)
        self.assertEqual(self.movies._lang_rank({'title': 'German Movie'}), 2)
        self.settings['pref_lang'] = 'any'
        self.assertEqual(self.movies._lang_rank({'lang': 'EN'}), 0)

    def test_playback_announces_all_alternatives_with_configured_path(self):
        self.movies.ListItem = Mock(return_value=Mock())
        self.movies.ListItemInfoTag = Mock()
        self.movies.play_directly = Mock()
        announce = Mock()
        hosts = [{'url': 'https://host.invalid/a'}, {'url': 'https://other.invalid/b'}]
        movie = {'title': 'Film', 'season': 0, 'episode': 1}
        with patch.dict(sys.modules, {'streamhub.failover': types.SimpleNamespace(announce=announce)}):
            self.movies._start_playback(movie, hosts[0], ('https://cdn.invalid/master.m3u8', None), hosts)
        announce.assert_called_once_with(movie, hosts[0], hosts, 'https://cdn.invalid/master.m3u8')

    def test_failover_resolves_fresh_and_preserves_auth_headers(self):
        source = {'url': 'https://host.invalid/a', 'lang': 'DE', '_alive': False,
                  '_stream': ('https://expired.invalid/old', None), '_resolved_at': 0}
        self.movies._resolve_and_verify = Mock(return_value=(source, (
            'https://cdn.invalid/new.mp4|Cookie=a%2Bb', 'Referer=https%3A%2F%2Fhost.invalid')))
        result = self.movies.resolve_failover_candidate(source, threading.Event())
        fresh = self.movies._resolve_and_verify.call_args.args[0]
        self.assertEqual(fresh, {'url': 'https://host.invalid/a', 'lang': 'DE'})
        self.assertIn('_stream', source)
        self.assertEqual(result[0], 'https://cdn.invalid/new.mp4')
        self.assertEqual(dict(parse_qsl(result[1])), {'Referer': 'https://host.invalid', 'Cookie': 'a+b'})

    def test_cancelled_failover_never_probes_and_discards_late_results(self):
        stop = threading.Event()
        stop.set()
        self.movies._resolve_and_verify = Mock()
        self.assertIsNone(self.movies.resolve_failover_candidate({}, stop))
        self.movies._resolve_and_verify.assert_not_called()
        stop.clear()
        def late(*args, **kwargs):
            stop.set()
            return {}, ('https://cdn.invalid/new.mp4', None)
        self.movies._resolve_and_verify.side_effect = late
        self.assertIsNone(self.movies.resolve_failover_candidate({}, stop))

    def test_failover_rejects_non_media_protocols(self):
        self.movies._resolve_and_verify = Mock(return_value=({}, ('plugin://other/addon', None)))
        self.assertIsNone(self.movies.resolve_failover_candidate({}, threading.Event()))

    def test_failover_honors_resolution_limit_changed_during_playback(self):
        self.settings['stream_quali'] = '2'
        self.movies._resolve_and_verify = Mock()
        host = {'url': 'https://host.invalid/a', 'quality': '1080p'}
        self.assertIsNone(self.movies.resolve_failover_candidate(host, threading.Event()))
        self.movies._resolve_and_verify.assert_not_called()

    def test_inline_auth_headers_reach_verification_without_changing_signed_url(self):
        url = 'https://cdn.invalid/master.m3u8?sig=a%2Bb&k=x'
        suffix = urlencode({'Cookie': 'token=a+b&c', 'Referer': 'https://host.invalid/a?b=c'})
        request = self.movies.request = Mock(return_value=types.SimpleNamespace(
            status_code=200, content=b'#EXTM3U\n', text='#EXTM3U\n'))
        self.assertTrue(self.movies._verify_stream(url+'|'+suffix, 'User-Agent=Agent+One'))
        self.assertEqual(request.call_args.args, ('GET', url))
        self.assertEqual(request.call_args.kwargs['headers']['Cookie'], 'token=a+b&c')
        self.assertEqual(request.call_args.kwargs['headers']['User-Agent'], 'Agent One')
        self.assertEqual(request.call_args.kwargs['max_bytes'], 8192)

    def test_short_extensionless_hls_survives_resolution_verification(self):
        url = 'https://cdn.invalid/manifest?id=abc'
        body = b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=500000\n720/index.m3u8\n'
        self.movies.request = Mock(return_value=types.SimpleNamespace(
            status_code=200, content=body, headers={'Content-Type': 'application/vnd.apple.mpegurl'}))
        self.movies._resolve_host = Mock(return_value=(url, 'Referer=https%3A%2F%2Fhost.invalid%2Fe%2Fa'))
        host = {'url': 'https://host.invalid/e/a'}
        resolved_host, stream = self.movies._resolve_and_verify(host)
        self.assertIs(resolved_host, host)
        self.assertEqual(stream[0], url)
        self.assertIs(host['_alive'], True)
        self.assertLess(len(body), 1024)
        self.assertEqual(self.movies.request.call_args.kwargs['max_bytes'], 65536)

    def test_extensionless_hls_signature_with_bom_and_plain_text_mime(self):
        body = b'\xef\xbb\xbf \r\n#EXTM3U\n#EXT-X-TARGETDURATION:6\npart.ts\n'
        self.movies.request = Mock(return_value=types.SimpleNamespace(
            status_code=200, content=body, headers={'content-type': 'text/plain; charset=utf-8'}))
        self.assertTrue(self.movies._verify_stream('https://cdn.invalid/hls/token', None))
        self.assertEqual(self.movies.request.call_args.kwargs['max_bytes'], 8192)

    def test_m3u8_query_value_does_not_turn_mp4_into_manifest(self):
        body = b'\x00\x00\x00\x18ftypisom' + b'\x00' * 2048
        url = 'https://cdn.invalid/video.mp4?return=https%3A%2F%2Fexample.invalid%2Fmaster.m3u8'
        self.movies.request = Mock(return_value=types.SimpleNamespace(
            status_code=206, content=body, headers={'Content-Type': 'video/mp4'}))
        self.assertTrue(self.movies._verify_stream(url, None))
        self.assertEqual(self.movies.request.call_args.args, ('GET', url))
        self.assertEqual(self.movies.request.call_args.kwargs['max_bytes'], 65536)

    def test_short_extensionless_dash_manifest_is_not_rejected_by_size(self):
        body = b'<?xml version="1.0"?>\n<MPD xmlns="urn:mpeg:dash:schema:mpd:2011"><Period/></MPD>'
        self.movies.request = Mock(return_value=types.SimpleNamespace(
            status_code=200, content=body, headers={'Content-Type': 'application/dash+xml'}))
        self.assertTrue(self.movies._verify_stream('https://cdn.invalid/manifest?id=dash', None))
        self.assertEqual(self.movies.request.call_args.kwargs['max_bytes'], 65536)

    def test_manifest_error_responses_are_not_playable(self):
        cases = [
            ('application/vnd.apple.mpegurl', b'<html><script>var example="#EXTM3U";</script></html>'),
            ('text/html', b'#EXTM3U\n<html>Temporarily unavailable</html>'),
            ('application/vnd.apple.mpegurl', b'<!--error-->Not found #EXTM3U'),
            ('application/dash+xml', b'<?xml version="1.0"?><Error>Access denied</Error>'),
            ('application/json', b'{"error":"temporarily unavailable"}' + b' ' * 2048),
            ('application/vnd.apple.mpegurl', b''),
        ]
        for content_type, body in cases:
            with self.subTest(content_type=content_type, body=body[:80]):
                self.movies.request = Mock(return_value=types.SimpleNamespace(
                    status_code=200, content=body, headers={'Content-Type': content_type}))
                self.assertFalse(self.movies._verify_stream('https://cdn.invalid/master.m3u8', None))

    def test_manifest_looking_url_can_redirect_to_mp4(self):
        self.movies.request = Mock(return_value=types.SimpleNamespace(
            status_code=200, content=b'\x00\x00\x00\x18ftypisom' + b'\x00' * 2048,
            headers={'Content-Type': 'video/mp4'}))
        self.assertTrue(self.movies._verify_stream('https://cdn.invalid/master.m3u8', None))

    def test_movie_setup_keeps_segment_auth_and_passes_clean_path(self):
        item = Mock()
        self.movies.ListItem = Mock(return_value=item)
        self.movies.ListItemInfoTag = Mock()
        self.movies.play_directly = Mock()
        self.movies._start_playback({'title': 'Test'}, {'label': 'Test'}, (
            'https://cdn.invalid/master.m3u8|Cookie=token%3Done', None))
        self.movies.play_directly.assert_called_once_with('https://cdn.invalid/master.m3u8', item)
        props = dict(call.args for call in item.setProperty.call_args_list)
        self.assertEqual(props['inputstream.adaptive.manifest_headers'], 'Cookie=token%3Done')
        self.assertEqual(props['inputstream.adaptive.stream_headers'], 'Cookie=token%3Done')
        self.assertEqual(props['inputstream.adaptive.chooser_resolution_max'], '1080p')

    def test_deep_check_caps_body_even_if_server_returns_video(self):
        self.movies._check_alive_cache_get = Mock(return_value=None)
        self.movies._check_alive_cache_set = Mock()
        self.movies.request = Mock(return_value=types.SimpleNamespace(
            status_code=200, headers={'content-type': 'video/mp4'}))
        self.assertTrue(self.movies.check_host_deep({'url': 'https://cdn.invalid/watch'}))
        self.assertEqual(self.movies.request.call_args.kwargs['max_bytes'], 80000)

    def test_head_fallback_never_reads_movie_body(self):
        self.movies.request = Mock(side_effect=[
            types.SimpleNamespace(status_code=405), types.SimpleNamespace(status_code=200)])
        self.assertTrue(self.movies._check_host_alive({'url': 'https://cdn.invalid/video'}))
        self.assertEqual(self.movies.request.call_args.kwargs['max_bytes'], 0)

    def test_quality_limit_uses_resolution_without_hdr_or_codec_bonus(self):
        hosts = [{'quality': '2160p'}, {'quality': '1080p HDR HEVC'},
                 {'quality': '720p'}, {'label': 'Unknown'}]
        self.assertEqual(self.movies._within_quality_limit(hosts), hosts[1:])
        self.settings['stream_quali'] = '2'
        self.assertEqual(self.movies._within_quality_limit(hosts), hosts[2:])
        self.settings['stream_quali'] = '0'
        self.assertEqual(self.movies._within_quality_limit(hosts), hosts)

    def test_external_resolver_receives_referer_and_preserves_headers(self):
        media = Mock()
        media.valid_url.return_value = True
        media.resolve.return_value = 'https://cdn.invalid/video.mp4|Cookie=a%2Bb'
        external = types.SimpleNamespace(HostedMediaFile=Mock(return_value=media))
        with patch.dict(sys.modules, {'resolveurl': external}):
            result = self.movies._resolve_external('https://host.invalid/e/id', 'https://provider.invalid/movie')
        self.assertEqual(result, ('https://cdn.invalid/video.mp4', 'Cookie=a%2Bb'))
        external.HostedMediaFile.assert_called_once_with(
            url='https://host.invalid/e/id$$https://provider.invalid/movie',
            include_disabled=False, include_popups=False)
        media.resolve.assert_called_once_with(allow_popups=False)

    def test_unsupported_external_host_is_not_resolved(self):
        media = Mock()
        media.valid_url.return_value = False
        with patch.dict(sys.modules, {'resolveurl': types.SimpleNamespace(HostedMediaFile=Mock(return_value=media))}):
            self.assertIsNone(self.movies._resolve_external('https://unknown.invalid/e/id'))
        media.resolve.assert_not_called()

    def test_external_success_avoids_duplicate_embed_download(self):
        native = types.SimpleNamespace(resolve=Mock(return_value=None), is_blocked=Mock(return_value=False))
        generic = types.SimpleNamespace(resolve_public_embed=Mock())
        self.movies._resolve_external = Mock(return_value=('https://cdn.invalid/stream.mp4', None))
        with patch.dict(sys.modules, {'streamhub.resolvers': native, 'streamhub.host_support': generic}):
            self.assertEqual(self.movies._resolve_embed({'url': 'https://host.invalid/e/a'}),
                             ('https://cdn.invalid/stream.mp4', None))
        native.resolve.assert_called_once_with('https://host.invalid/e/a', 'https://host.invalid/e/a', generic=False)
        generic.resolve_public_embed.assert_not_called()

    def test_protected_native_host_does_not_retry_external_or_generic(self):
        state = {'failure': None}
        def native_resolve(*args, **kwargs):
            state['failure'] = 'protected'
        native = types.SimpleNamespace(resolve=Mock(side_effect=native_resolve),
            is_blocked=Mock(return_value=False),
            get_resolve_failure=lambda url: state['failure'])
        generic = types.SimpleNamespace(resolve_public_embed=Mock())
        self.movies._resolve_external = Mock()
        host = {'url': 'https://dr0pstream.com/e/fixture'}
        with patch.dict(sys.modules, {'streamhub.resolvers': native, 'streamhub.host_support': generic}):
            self.assertIsNone(self.movies._resolve_embed(host))
            self.assertIsNone(self.movies._resolve_embed(host))
        native.resolve.assert_called_once()
        self.movies._resolve_external.assert_not_called()
        generic.resolve_public_embed.assert_not_called()
        self.assertEqual(host['_failure'], 'protected')

    def test_vavoo_chain_stops_after_observed_host_protection(self):
        source = SOURCE / 'vjackson.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'resolve')
        external = Mock()
        remote = Mock(side_effect=AssertionError('Protected host must not be retried'))
        namespace = {'log': Mock(), 'resolveurl': external, 'request_json': remote,
                     'getAuthSignature': remote, 'format_exc': lambda: 'test error', 'callApi2': remote}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), namespace)
        native = types.SimpleNamespace(resolve=Mock(return_value=None),
            get_resolve_failure=lambda url: 'protected')
        with patch.dict(sys.modules, {'streamhub.resolvers': native}):
            self.assertIsNone(namespace['resolve']({'url': 'https://doodstream.com/e/fixture'}))
        native.resolve.assert_called_once()
        external.resolve.assert_not_called()
        remote.assert_not_called()

    def test_protected_host_is_labelled_as_protected_not_dead(self):
        label = self.movies._host_display({'host': 'Dood', '_failure': 'protected', '_alive': False})
        self.assertIn('Schutzpruefung'.replace('ue', '\u00fc'), label)
        self.assertNotIn('[COLOR red]tot', label)

    def test_protected_picker_failure_is_explained_without_claiming_dead_file(self):
        self.settings['fresh_start'] = 'false'
        host = {'url': 'https://dr0pstream.com/e/fixture', '_failure': 'protected'}
        self.movies._resolve_host = Mock(return_value=None)
        self.movies.selectDialog = Mock(return_value=0)
        self.movies.fail_resolved = Mock()
        native = types.SimpleNamespace(get_resolve_failure=lambda url: 'protected')
        with patch.dict(sys.modules, {'streamhub.resolvers': native}):
            self.movies.choose_and_play({}, [host], 'Film', Mock(), True, False)
        self.assertIsNone(host['_alive'])
        self.assertIn('Schutzpruefung'.replace('ue', '\u00fc'), self.movies.fail_resolved.call_args.args[0])

    def test_dood_source_does_not_probe_signed_cdn_url(self):
        self.movies.request = Mock()
        with patch.dict(sys.modules, {'streamhub.resolvers': types.SimpleNamespace(is_dood_url=lambda u: True)}):
            self.assertIsNone(self.movies._verify_stream('https://storage.invalid/video?signature=x',
                                                        None, {'url': 'https://do7go.com/e/id'}))
        self.movies.request.assert_not_called()

    def test_picker_refreshes_aged_resolved_token_only(self):
        self.settings['fresh_start'] = 'false'
        for age, expected in ((90, 'new'), (5, 'old')):
            with self.subTest(age=age):
                host = {'url': 'https://dood.invalid/e/a', '_resolved_at': time.monotonic() - age}
                self.movies._resolve_verify_bulk = Mock(return_value=[(host, ('old', None))])
                self.movies.selectDialog = Mock(return_value=0)
                self.movies._start_playback = Mock()
                self.movies._resolve_host = Mock(return_value=('new', None))
                self.movies._host_display = Mock(return_value='Host')
                self.movies.choose_and_play({}, [host, {'url': 'https://other.invalid/e/b'}],
                                            'Title', Mock(), True, True)
                self.assertEqual(self.movies._start_playback.call_args.args[2][0], expected)
                self.assertEqual(self.movies._resolve_host.call_count, 1 if age >= 60 else 0)

    def test_opaque_url_tokens_and_provider_names_do_not_become_hard_limits(self):
        hosts = [{'url': 'https://cdn.invalid/ab4kXYZ'},
                 {'label': 'movie4k', 'url': 'https://cdn.invalid/id1080p'},
                 {'host': '4k.example.invalid'}, {'quality': '1080p'}]
        self.assertEqual(self.movies._within_quality_limit(hosts), hosts)

    def test_no_bandwidth_probe_after_another_host_wins(self):
        stop = threading.Event()
        def resolve(host, stop=None):
            stop.set()
            return 'https://cdn.invalid/video.mp4', None
        self.movies._resolve_host = Mock(side_effect=resolve)
        self.movies._verify_stream = Mock()
        host = {}
        self.assertEqual(self.movies._resolve_and_verify(host, stop), (host, None))
        self.movies._verify_stream.assert_not_called()

    def test_single_dead_host_is_not_retried_without_verification(self):
        self.movies._resolve_host = Mock(return_value=('https://cdn.invalid/video.mp4', None))
        self.movies._verify_stream = Mock(return_value=False)
        self.assertEqual(self.movies._race_hosts([{}], verify=True), (None, None))
        self.movies._resolve_host.assert_called_once()

    def test_expired_race_deadline_does_not_trigger_serial_retries(self):
        self.movies._resolve_host = Mock(return_value=None)
        self.assertEqual(self.movies._race_hosts([{}, {}], deadline_s=-1), (None, None))
        self.assertLessEqual(self.movies._resolve_host.call_count, 2)


    def test_single_slow_host_obeys_race_deadline(self):
        release = threading.Event()
        self.movies._resolve_host = Mock(side_effect=lambda *a, **kw: release.wait(2) and None)
        started = time.monotonic()
        try:
            self.assertEqual(self.movies._race_hosts([{}], deadline_s=0.03), (None, None))
            self.assertLess(time.monotonic() - started, 0.4)
        finally:
            release.set()

    def test_ready_unknown_language_host_does_not_wait_for_other_unknown_hosts(self):
        release = threading.Event()
        fast, slow = {'url': 'fast'}, {'url': 'slow'}
        def resolve(host, **kwargs):
            if host is slow:
                release.wait(2)
                return None
            return ('https://cdn.invalid/video.mp4', None)
        self.movies._resolve_host = Mock(side_effect=resolve)
        started = time.monotonic()
        try:
            chosen, stream = self.movies._race_hosts([fast, slow], deadline_s=1)
            self.assertIs(chosen, fast)
            self.assertIsNotNone(stream)
            self.assertLess(time.monotonic() - started, 0.4)
        finally:
            release.set()

    def test_slow_preferred_language_still_beats_ready_fallback(self):
        fallback, preferred = {'lang': 'EN'}, {'lang': 'DE'}
        def resolve(host, **kwargs):
            if host is preferred:
                time.sleep(0.03)
            return ('https://cdn.invalid/video.mp4', None)
        self.movies._resolve_host = Mock(side_effect=resolve)
        self.assertIs(self.movies._race_hosts([fallback, preferred], deadline_s=1)[0], preferred)

    def test_cancelled_single_host_does_not_start_resolver(self):
        self.movies._resolve_host = Mock()
        self.movies._TOKEN = types.SimpleNamespace(cancelled=lambda: True, cancel=Mock())
        self.assertEqual(self.movies._race_hosts([{}]), (None, None))
        self.movies._resolve_host.assert_not_called()

    def test_cancelled_gather_returns_without_waiting_for_provider(self):
        release, cancelled = threading.Event(), threading.Event()
        def get_hosts(hit):
            cancelled.set()
            release.wait(2)
            return []
        provider = types.SimpleNamespace(name='slow', get_hosts=get_hosts)
        self.movies._TOKEN = types.SimpleNamespace(cancelled=cancelled.is_set)
        self.movies._enabled_providers = Mock(return_value=[provider])
        started = time.monotonic()
        try:
            self.assertEqual(self.movies._gather_hosts({'hits': [{'provider': 'slow'}]}), [])
            self.assertLess(time.monotonic() - started, 0.4)
        finally:
            release.set()

    def test_movie_cache_identity_includes_enabled_sources_year_and_language(self):
        self.movies.StatusProgress = Mock()
        self.movies.get_cache = Mock(return_value=(True, [{'url': 'cached'}]))
        self.movies.choose_and_play = Mock()
        provider = types.SimpleNamespace(name='movie2k')
        self.movies._enabled_providers = Mock(return_value=[provider])
        movie = {'title': 'Remake', 'year': 2001}
        self.movies._play_common(movie, movie, 'Remake')
        key = self.movies.get_cache.call_args.args[0]
        self.assertEqual(key['providers'], ['movie2k'])
        self.assertEqual(key['year'], '2001')
        self.assertEqual(key['lang'], 'DE')
        self.movies._enabled_providers.return_value = []
        self.movies._play_common(dict(movie, year=2026), movie, 'Remake')
        self.assertNotEqual(key, self.movies.get_cache.call_args.args[0])

    def test_abandoned_native_resolution_skips_fallback_network_requests(self):
        stop = threading.Event()
        def native_resolve(*args, **kwargs):
            stop.set()
        native = types.SimpleNamespace(resolve=Mock(side_effect=native_resolve),
            is_blocked=lambda url: False, get_resolve_failure=lambda url: None)
        self.movies._resolve_external = Mock()
        generic = types.SimpleNamespace(resolve_public_embed=Mock())
        with patch.dict(sys.modules, {'streamhub.resolvers': native, 'streamhub.host_support': generic}):
            self.assertIsNone(self.movies._resolve_embed({'url': 'https://host.invalid/a'}, stop=stop))
        self.movies._resolve_external.assert_not_called()
        generic.resolve_public_embed.assert_not_called()


if __name__ == '__main__':
    unittest.main()
