"""Recovery state-machine tests with a fake Kodi player and deterministic jobs."""
import importlib.util
import json
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[1] / 'src/plugin.video.streamhub/resources/streamhub'


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, SOURCE / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FailoverTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.settings = {'auto_host_failover': 'true', 'pref_lang': 'de', 'resume_enabled': 'true'}
        self.state = {'playing': True, 'url': 'https://cdn.test/original.m3u8',
                      'pos': 240.25, 'total': 1200.0, 'caching': False,
                      'paused': False, 'seeking': False, 'canseek': True}
        self.audio = [{'index': 0, 'language': 'deu'}]
        self.current_audio = 0
        self.props = {}
        self.requests = []
        self.notifications = []
        self.jobs = []
        owner = self

        class Player:
            def isPlaying(self): return owner.state['playing']
            def isPlayingVideo(self): return owner.state['playing']
            def getPlayingFile(self):
                if not owner.state['playing']:
                    raise RuntimeError('not playing')
                return owner.state['url']
            def getTime(self): return owner.state['pos']
            def getTotalTime(self): return owner.state['total']
            def play(self, url, item):
                owner.requests.append((url, item))
                owner.state.update(url=url, pos=0, playing=True, caching=False, paused=False)
            def seekTime(self, value):
                owner.seeks.append(value)
                owner.state['pos'] = value
                self.onPlayBackSeek(value * 1000, 0)
            def setAudioStream(self, index):
                owner.audio_selected.append(index)
                owner.current_audio = index
            def pause(self):
                owner.state['paused'] = not owner.state['paused']
                self.onPlayBackPaused() if owner.state['paused'] else self.onPlayBackResumed()
            def stop(self):
                owner.state['playing'] = False
                self.onPlayBackStopped()

        class Item:
            def __init__(self, **kwargs): self.properties = {}; self.path = ''; self.mime = ''
            def setProperty(self, name, value): self.properties[name] = value
            def setPath(self, value): self.path = value
            def setMimeType(self, value): self.mime = value
            def setContentLookup(self, value): pass
            def getVideoInfoTag(self): return types.SimpleNamespace(setTitle=lambda value: None)

        class DeferredThread:
            def __init__(self, target, name, daemon):
                self.target, self.daemon = target, daemon
            def start(self): owner.jobs.append(self)

        self.seeks, self.audio_selected = [], []
        kodi = types.ModuleType('xbmc')
        kodi.Player = Player
        condition_keys = {'Player.Caching': 'caching', 'Player.Paused': 'paused',
                          'Player.Seeking': 'seeking', 'Player.SeekEnabled': 'canseek'}
        kodi.getCondVisibility = lambda name: self.state.get(condition_keys.get(name), False)
        kodi.getInfoLabel = lambda _: '21.3'
        self.rpc_calls = []
        def rpc(raw):
            req = json.loads(raw)
            self.rpc_calls.append(req)
            if req['method'] == 'Player.GetActivePlayers':
                result = [{'type': 'video', 'playerid': 1}]
            else:
                current = next((dict(s) for s in self.audio if s['index'] == self.current_audio), {})
                result = {'audiostreams': self.audio, 'currentaudiostream': current}
            return json.dumps({'result': result})
        kodi.executeJSONRPC = rpc
        home = types.SimpleNamespace(getProperty=lambda key: self.props.get(key, ''),
                                    setProperty=lambda key, value: self.props.__setitem__(key, value),
                                    clearProperty=lambda key: self.props.pop(key, None))
        gui = types.ModuleType('xbmcgui')
        gui.Window, gui.ListItem = lambda _: home, Item
        gui.Dialog = lambda: types.SimpleNamespace(notification=lambda *args, **kw: self.notifications.append(args))
        utils = types.SimpleNamespace(getSetting=lambda name: self.settings.get(name, ''))
        package = types.ModuleType('streamhub')
        self.resolve = Mock(return_value=('https://cdn.test/new.m3u8', 'Referer=https%3A%2F%2Fembed.test'))
        self.modules = patch.dict(sys.modules, {
            'xbmc': kodi, 'xbmcgui': gui,
            'xbmcaddon': types.SimpleNamespace(Addon=lambda _: types.SimpleNamespace(getLocalizedString=lambda _: '')),
            'streamhub': package, 'streamhub.utils': utils,
            'streamhub.resume': types.SimpleNamespace(key_for=lambda movie: 't123'),
            'streamhub.movies': types.SimpleNamespace(resolve_failover_candidate=self.resolve),
        })
        self.modules.start()
        self.addCleanup(self.modules.stop)
        sys.modules['streamhub.language'] = load('streamhub.language', 'language.py')
        sys.modules['streamhub.playback'] = load('streamhub.playback', 'playback.py')
        self.f = load('test_failover_module', 'failover.py')
        self.f.time = types.SimpleNamespace(monotonic=lambda: self.now, time=lambda: self.now + 100000)
        self.f.threading = types.SimpleNamespace(Event=threading.Event, Thread=DeferredThread)
        self.p = self.f.FailoverPlayer()
        self.addCleanup(self.p.shutdown)
        self.current = {'url': 'https://embed.test/original', 'host': 'Original', 'lang': 'DE'}
        self.other = {'url': 'https://embed.test/other', 'host': 'Other', 'lang': 'DE'}

    def start(self, hosts=None):
        self.f.announce({'title': 'Film'}, self.current, hosts or [self.current, self.other], self.state['url'])
        self.p.onPlayBackStarted()
        self.p.onAVStarted()
        self.p.sample()

    def tick(self, seconds=1):
        self.now += seconds
        self.p.sample()

    def trigger(self):
        self.tick(31)
        self.state['caching'] = True
        self.tick()
        self.tick(20)
        self.assertEqual(len(self.jobs), 1)

    def complete_job(self, result=None):
        if result is not None:
            self.resolve.return_value = result
        job = self.jobs.pop(0)
        self.assertTrue(job.daemon)
        job.target()
        self.tick()

    def av(self, native_resume=True):
        self.p.onPlayBackStarted()
        if native_resume:
            self.state['pos'] = float(self.requests[-1][1].properties['StartOffset'])
        self.p.onAVStarted()
        self.tick()
        if self.p._transition and self.p._transition.get('resume_confirmed'):
            self.state['pos'] += 1
            self.tick()

    def test_announce_bounds_payload_and_removes_cached_resolutions(self):
        pool = [dict(self.other, url='https://embed.test/%d' % i, _stream=['signed'], _resolved_at=5) for i in range(200)]
        self.f.announce({'title': 'Film'}, self.current, pool, self.state['url'])
        data = json.loads(self.props[self.f.PROPERTY])
        self.assertEqual(len(data['hosts']), 32)
        self.assertNotIn('_stream', data['hosts'][0])
        self.assertNotIn('_resolved_at', data['hosts'][0])
        self.assertEqual(data['current']['url'], self.current['url'])

    def test_setting_disabled_never_resolves_or_switches(self):
        self.settings['auto_host_failover'] = 'false'
        self.start()
        self.state['caching'] = True
        self.tick(100)
        self.tick(100)
        self.assertEqual(self.jobs, [])
        self.assertEqual(self.requests, [])

    def test_only_sustained_actual_buffering_after_startup_grace_triggers(self):
        self.start()
        self.state['caching'] = True
        self.tick(20)
        self.assertEqual(self.jobs, [])
        self.tick(11)
        self.tick(19)
        self.assertEqual(self.jobs, [])
        self.tick(1)
        self.assertEqual(len(self.jobs), 1)

    def test_stationary_position_without_caching_never_triggers(self):
        self.start()
        self.tick(100)
        self.tick(100)
        self.assertEqual(self.jobs, [])

    def test_short_buffering_resets_threshold(self):
        self.start()
        self.tick(31)
        self.state['caching'] = True
        self.tick()
        self.tick(19)
        self.state['caching'] = False
        self.tick()
        self.state['caching'] = True
        self.tick()
        self.tick(19)
        self.assertEqual(self.jobs, [])
        self.tick()
        self.assertEqual(len(self.jobs), 1)

    def test_result_is_discarded_if_original_recovers(self):
        self.start(); self.trigger()
        self.state['caching'] = False
        self.complete_job()
        self.assertEqual(self.requests, [])

    def test_progress_cancels_resolved_replacement_even_if_caching_flag_lags(self):
        self.start(); self.trigger()
        self.state['pos'] += 2
        self.complete_job()
        self.assertEqual(self.requests, [])

    def test_pause_seek_stop_and_toggle_off_cancel_inflight_resolution(self):
        for action in ('pause', 'seek', 'stop', 'off'):
            with self.subTest(action=action):
                self.p.shutdown(); self.p = self.f.FailoverPlayer(); self.jobs.clear()
                self.state.update(playing=True, url='https://cdn.test/original.m3u8', caching=False, paused=False)
                self.settings['auto_host_failover'] = 'true'
                self.start(); self.trigger()
                if action == 'pause': self.p.onPlayBackPaused()
                elif action == 'seek': self.p.onPlayBackSeek(300000, 60000)
                elif action == 'stop': self.p.onPlayBackStopped()
                else: self.settings['auto_host_failover'] = 'false'
                self.p.sample()
                self.complete_job()
                self.assertEqual(self.requests, [])

    def test_seek_has_fifteen_second_grace_before_new_buffer_window(self):
        self.start(); self.tick(31)
        self.state['caching'] = True
        self.p.onPlayBackSeek(300000, 60000); self.p.sample()
        self.tick(14)
        self.assertEqual(self.jobs, [])
        self.tick(1); self.tick(19)
        self.assertEqual(self.jobs, [])
        self.tick(1)
        self.assertEqual(len(self.jobs), 1)

    def test_new_title_invalidates_old_job_result(self):
        self.start(); self.trigger()
        self.state['url'] = 'https://cdn.test/different.m3u8'
        self.f.announce({'title': 'Different'}, self.current, [self.other], self.state['url'])
        self.p.onAVStarted(); self.p.sample()
        self.complete_job()
        self.assertEqual(self.requests, [])

    def test_queued_old_stop_before_announcement_does_not_clear_new_session(self):
        self.p.onPlayBackStopped()
        self.now += 1
        self.start()
        self.assertIsNotNone(self.p._session)
        self.assertTrue(self.p._av)

    def test_wrong_and_unknown_provider_languages_are_not_candidates(self):
        self.start(hosts=[self.current, dict(self.other, lang='EN'), dict(self.other, url='https://embed.test/u', lang=''),
                          dict(self.other, url='https://embed.test/sub', lang='DE-SUB')])
        self.tick(31); self.state['caching'] = True; self.tick(); self.tick(20)
        self.assertEqual(self.jobs, [])
        self.assertTrue(self.p._exhausted)

    def test_any_language_allows_candidate_and_skips_audio_rpc(self):
        self.settings['pref_lang'] = 'any'
        self.start(hosts=[self.current, dict(self.other, lang='')])
        self.assertEqual(self.rpc_calls, [])
        self.trigger(); self.complete_job(); self.av()
        self.assertEqual(self.p._handoffs, 1)
        self.assertEqual(self.rpc_calls, [])

    def test_exact_fractional_resume_works_without_persistent_resume_under_thirty_seconds(self):
        self.settings['resume_enabled'] = 'false'
        self.state['pos'] = 12.25
        self.start(); self.trigger(); self.complete_job()
        self.assertEqual(self.requests[-1][1].properties['StartOffset'], '12.25')
        self.av()
        self.assertEqual(self.seeks, [])
        self.assertEqual(self.p._handoffs, 1)
        self.assertNotIn('streamhub_playing', self.props)

    def test_native_startoffset_seek_callback_does_not_cancel_replacement(self):
        self.start(); self.trigger(); self.complete_job()
        self.state['pos'] = 240.25
        self.p.onPlayBackSeek(240250, 240250)
        self.av()
        self.assertEqual(self.p._handoffs, 1)
        self.assertEqual(self.seeks, [])

    def test_fallback_seek_issued_once_and_acknowledged_before_acceptance(self):
        self.start(); self.trigger(); self.complete_job(); self.av(native_resume=False)
        self.assertEqual(self.p._handoffs, 0)
        self.tick()
        self.assertEqual(self.seeks, [240.25])
        self.tick()
        self.state['pos'] += 1
        self.tick()
        self.assertEqual(self.seeks, [240.25])
        self.assertEqual(self.p._handoffs, 1)
        packet = json.loads(self.props['streamhub_playing'])
        self.assertTrue(packet['managed_resume'])
        self.assertEqual(packet['resume'], 241.25)

    def test_manual_seek_to_other_position_during_open_cancels_recovery(self):
        self.start(); self.trigger(); self.complete_job()
        self.p.onPlayBackSeek(500000, 500000); self.p.sample()
        self.assertIsNone(self.p._session)
        self.assertFalse(self.f.transition_active())

    def test_actual_matching_audio_selected_and_confirmed(self):
        self.start(); self.trigger(); self.complete_job()
        self.audio = [{'index': 0, 'language': 'eng'}, {'index': 1, 'language': 'ger'}]
        self.av()
        self.assertEqual(self.audio_selected, [1])
        self.assertEqual(self.p._handoffs, 0)
        self.tick()
        self.state['pos'] += 1
        self.tick()
        self.assertEqual(self.p._handoffs, 1)

    def test_matching_current_audio_is_kept_instead_of_switching_to_commentary(self):
        self.audio = [{'index': 0, 'language': 'deu', 'name': 'German commentary'},
                      {'index': 1, 'language': 'deu', 'name': 'German main'}]
        self.current_audio = 1
        self.start(); self.trigger(); self.complete_job(); self.av()
        self.assertEqual(self.audio_selected, [])
        self.assertEqual(self.p._handoffs, 1)

    def test_replacement_must_exit_buffering_and_advance_before_acceptance(self):
        self.start(); self.trigger(); self.complete_job()
        self.state.update(pos=240.25, caching=True)
        self.p.onPlayBackStarted(); self.p.onAVStarted(); self.tick()
        self.state['pos'] += 1
        self.tick()
        self.assertEqual(self.p._handoffs, 0)
        self.state['caching'] = False
        self.tick()
        self.assertEqual(self.p._handoffs, 1)

    def test_stationary_replacement_without_caching_does_not_count_as_success(self):
        self.start(); self.trigger(); self.complete_job()
        self.state['pos'] = 240.25
        self.p.onPlayBackStarted(); self.p.onAVStarted(); self.tick()
        self.tick(5)
        self.assertEqual(self.p._handoffs, 0)
        self.assertNotIn('streamhub_playing', self.props)
        self.tick(25)
        self.assertTrue(self.p._base['restored'])

    def test_unknown_audio_rejects_candidate_and_restores_original_without_loop(self):
        self.start(); self.trigger(); self.complete_job()
        self.audio = [{'index': 0, 'language': 'und'}]
        self.av(); self.tick(8)
        self.assertTrue(self.state['paused'])
        self.assertEqual(len(self.jobs), 1)
        self.complete_job(('https://cdn.test/original.m3u8', None))
        self.av()
        self.assertEqual(self.p._handoffs, 0)
        self.assertTrue(self.p._exhausted)
        self.assertEqual(self.state['url'], 'https://cdn.test/original.m3u8')
        self.state['caching'] = True
        self.tick(120); self.tick(30)
        self.assertEqual(self.jobs, [])

    def test_wrong_audio_does_not_pass_provider_german_label(self):
        self.start(); self.trigger(); self.complete_job()
        self.audio = [{'index': 0, 'language': 'eng'}]
        self.av(); self.tick(8)
        self.assertEqual(self.p._handoffs, 0)
        self.assertTrue(self.p._base['restored'])

    def test_gross_duration_mismatch_is_rejected_before_resume(self):
        self.start(); self.trigger(); self.complete_job()
        self.state['total'] = 1600
        self.av()
        self.assertEqual(self.p._handoffs, 0)
        self.assertTrue(self.p._base['restored'])
        self.assertEqual(self.seeks, [])

    def test_no_confirmed_audio_warns_initial_user_selection_without_stopping(self):
        self.audio = [{'index': 0, 'language': 'und'}]
        self.settings['auto_host_failover'] = 'false'
        self.start(); self.tick(9)
        self.assertTrue(self.state['playing'])
        self.assertTrue(any('could not be confirmed' in note[1] for note in self.notifications))
        self.tick(20)
        self.assertEqual(len(self.notifications), 1)

    def test_duplicate_resolved_media_is_skipped_without_restarting(self):
        self.start(); self.trigger()
        self.complete_job(('https://cdn.test/original.m3u8|Cookie=abc', None))
        self.assertEqual(self.requests, [])
        self.assertTrue(self.p._exhausted)

    def test_changed_preference_rechecks_candidate_before_handoff(self):
        self.start(); self.trigger()
        self.settings['pref_lang'] = 'en'
        self.complete_job()
        self.assertEqual(self.requests, [])
        self.assertTrue(self.p._exhausted)

    def test_candidate_failures_are_limited_to_six(self):
        self.start(hosts=[self.current] + [dict(self.other, url='https://embed.test/%d' % i) for i in range(12)])
        self.trigger()
        self.resolve.return_value = None
        for _ in range(6): self.complete_job()
        self.assertEqual(self.resolve.call_count, 6)
        self.assertEqual(self.jobs, [])
        self.assertTrue(self.p._exhausted)

    def test_handoffs_limited_to_three_and_cooldown_prevents_thrashing(self):
        self.start(); self.trigger(); self.complete_job(); self.av()
        self.state['caching'] = True
        self.tick(59)
        self.assertEqual(self.jobs, [])
        self.p._handoffs = 3
        self.tick(100); self.tick(25)
        self.assertEqual(self.jobs, [])

    def test_stop_after_replacement_started_is_not_ignored(self):
        self.start(); self.trigger(); self.complete_job()
        self.p.onPlayBackStarted(); self.p.sample()
        self.p.onPlayBackStopped(); self.p.sample()
        self.assertIsNone(self.p._session)
        self.assertFalse(self.f.transition_active())

    def test_immediate_candidate_error_advances_to_restore(self):
        self.start(); self.trigger(); self.complete_job()
        self.state['playing'] = False
        self.p.onPlayBackError(); self.p.sample()
        self.assertEqual(len(self.jobs), 1)
        self.assertTrue(self.p._base['restored'])
        self.assertTrue(self.f.transition_active())

    def test_hung_restore_does_not_suppress_tracking_forever_or_spawn_workers(self):
        self.start(); self.trigger(); self.complete_job()
        self.audio = [{'index': 0, 'language': 'eng'}]
        self.av(); self.tick(8)
        self.assertEqual(len(self.jobs), 1)
        self.tick(26)
        self.assertFalse(self.f.transition_active())
        self.assertFalse(self.state['playing'])
        self.assertEqual(len(self.jobs), 1)
        self.assertTrue(self.p._job['stop'].is_set())

    def test_foreign_playback_without_callback_wins_over_ready_restore(self):
        self.start(); self.trigger(); self.complete_job()
        self.audio = [{'index': 0, 'language': 'eng'}]
        self.av(); self.tick(8)
        self.assertEqual(len(self.jobs), 1)
        self.state.update(url='https://other-addon.test/user-video.mp4', playing=True)
        before = len(self.requests)
        self.complete_job(('https://cdn.test/original.m3u8', None))
        self.assertEqual(len(self.requests), before)
        self.assertEqual(self.state['url'], 'https://other-addon.test/user-video.mp4')
        self.assertIsNone(self.p._session)
        self.assertFalse(self.f.transition_active())

    def test_restore_timeout_cannot_stop_foreign_playback(self):
        self.start(); self.trigger(); self.complete_job()
        self.audio = [{'index': 0, 'language': 'eng'}]
        self.av(); self.tick(8)
        self.state.update(url='https://other-addon.test/user-video.mp4', playing=True)
        self.tick(26)
        self.assertTrue(self.state['playing'])
        self.assertEqual(self.state['url'], 'https://other-addon.test/user-video.mp4')
        self.assertFalse(self.f.transition_active())

    def test_known_nonseekable_stream_does_not_switch_away(self):
        self.start(); self.tick(31)
        self.state.update(caching=True, canseek=False)
        self.tick(); self.tick(100)
        self.assertEqual(self.jobs, [])

    def test_seek_capability_uses_kodis_documented_boolean_name(self):
        seen = []
        old = self.f.xbmc.getCondVisibility
        self.f.xbmc.getCondVisibility = lambda condition: (seen.append(condition), old(condition))[1]
        self.p._conditions()
        self.assertIn('Player.SeekEnabled', seen)
        self.assertNotIn('Player.CanSeek', seen)

    def test_shutdown_cancels_daemon_and_late_result_never_plays(self):
        self.start(); self.trigger()
        self.p.shutdown()
        self.complete_job()
        self.assertEqual(self.requests, [])
        self.assertFalse(self.f.transition_active())


if __name__ == '__main__':
    unittest.main()
