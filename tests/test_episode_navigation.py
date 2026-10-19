"""Available-episode navigation shared by the service and resume menu."""
import ast
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

RESOURCES = Path(__file__).resolve().parents[1] / "src/plugin.video.streamhub/resources"
spec = importlib.util.spec_from_file_location("streamhub.episode_navigation", RESOURCES / "streamhub/episode_navigation.py")
navigation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(navigation)


def functions(path, names, namespace):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


class EpisodeNavigationTests(unittest.TestCase):
    def test_explicit_numbers_skip_gaps_and_stop_at_last_available_episode(self):
        self.assertEqual(navigation.next_episode_number(1, 8, '[1, 3, 8]'), 3)
        self.assertEqual(navigation.next_episode_number(3, 8, [8, 1, 3, 8]), 8)
        self.assertIsNone(navigation.next_episode_number(8, 8, [1, 3, 8]))

    def test_legacy_count_remains_supported(self):
        self.assertEqual(navigation.next_episode_number(1, 3), 2)
        self.assertIsNone(navigation.next_episode_number(3, 3))
        self.assertEqual(navigation.next_episode_number(1), 2)

    def test_invalid_or_empty_explicit_metadata_does_not_invent_episodes(self):
        for available in ('bad', '{}', '[]', [], {}, [False, None, 'invalid'], 'x' * 65537):
            with self.subTest(available=str(available)[:30]):
                self.assertIsNone(navigation.next_episode_number(1, 10, available))
        self.assertIsNone(navigation.next_episode_number('bad', 10))

    def test_real_service_uses_available_numbers_and_preserves_them(self):
        kodi = Mock()
        params = {'action': 'series_play', 'series': '{}', 'season': '1',
                  'episode': '1', 'ep_count': '3', 'episode_numbers': '[1, 3]'}
        resume = types.SimpleNamespace(get_meta=lambda key: {'params': params, 'episode_count': 3})
        package = types.ModuleType('streamhub')
        package.resume = resume
        namespace = functions(RESOURCES / 'service.py', {'_autoplay_next'}, {
            '_setting': lambda key: 'true', '_log': Mock(), 'xbmc': kodi, 'ADDON_ID': 'plugin.video.streamhub'})
        with patch.dict(sys.modules, {'streamhub': package, 'streamhub.resume': resume,
                                     'streamhub.episode_navigation': navigation}):
            namespace['_autoplay_next'](Mock(), 't1:s1:e1')
            result = parse_qs(urlsplit(kodi.Player.return_value.play.call_args.args[0]).query)
            self.assertEqual(result['episode'], ['3'])
            self.assertEqual(result['episode_numbers'], ['[1, 3]'])
            kodi.Player.return_value.play.reset_mock()
            params['episode'] = '3'
            namespace['_autoplay_next'](Mock(), 't1:s1:e3')
            kodi.Player.return_value.play.assert_not_called()

    def test_real_resume_menu_skips_gaps_and_stops_after_known_catalog(self):
        row = {'kind': 'episode', 'watched': True, 'season': 1, 'episode': 1,
               'episode_count': 3, 'params': {'episode_numbers': '[1, 3]'}}
        namespace = functions(RESOURCES / 'streamhub/resume.py', {'_se', 'next_up'}, {
            '_load': lambda: {'t1:s1:e1': row}, '_MIN_RESUME': 30})
        with patch.dict(sys.modules, {'streamhub.episode_navigation': navigation}):
            self.assertEqual(namespace['next_up']('1'), {'season': '1', 'episode': '3', 'resume': False})
            row['episode'] = 3
            self.assertIsNone(namespace['next_up']('1'))


if __name__ == '__main__':
    unittest.main()
