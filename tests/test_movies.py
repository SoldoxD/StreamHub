"""Offline regression tests for the real movie selection/playback integration."""
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
        self.playback = load('streamhub.playback', 'playback.py')
        sys.modules['streamhub.playback'] = self.playback
        self.movies = load('movie_regressions', 'movies.py')

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
        def resolve(host):
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


if __name__ == '__main__':
    unittest.main()
