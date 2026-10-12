"""The real provider registry must expose new sources through movie adapters."""
import importlib
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

RESOURCES = Path(__file__).resolve().parents[1] / 'src/plugin.video.streamhub/resources'


class ProviderRegistryTests(unittest.TestCase):
    def setUp(self):
        settings = ET.parse(RESOURCES / 'settings.xml').getroot()
        self.settings = {node.get('id'): node.findtext('default') or '' for node in settings.iter('setting')}
        cache = tempfile.TemporaryDirectory()
        self.addCleanup(cache.cleanup)
        utils = types.ModuleType('streamhub.utils')
        utils.getSetting = lambda key: self.settings.get(key, '')
        utils.log = Mock()
        utils.format_exc = lambda: 'fixture error'
        utils.cachepath = cache.name
        utils.request = Mock(side_effect=AssertionError('No network in registry test'))
        utils.request_json = utils.request
        utils.getAuthSignature = Mock()
        modules = patch.dict(sys.modules, {'streamhub.utils': utils})
        modules.start()
        self.addCleanup(modules.stop)
        # Force independent imports; other fixture tests replace these modules.
        for name in list(sys.modules):
            if name == 'lib' or name.startswith('lib.') or name == 'streamhub':
                sys.modules.pop(name, None)
        path = patch.object(sys, 'path', [str(RESOURCES)] + sys.path)
        path.start()
        self.addCleanup(path.stop)
        self.registry = importlib.import_module('lib.scrapers')
        self.adapter = importlib.import_module('lib.scrapers.adapter')

    def test_new_movie_sources_reach_adapters_with_default_settings(self):
        names = {provider.name for provider in self.adapter.movie_providers()}
        self.assertIn('hdfilme', names)
        self.assertIn('streamkiste', names)
        self.assertIn('vavoo', names)
        self.assertIn('movie4k', names)
        self.assertNotIn('kinox', names)

    def test_disabled_new_sources_are_not_scheduled_for_search(self):
        self.settings.update(hdfilme='false', streamkiste='false')
        names = {provider.name for provider in self.adapter.movie_providers()}
        self.assertNotIn('hdfilme', names)
        self.assertNotIn('streamkiste', names)

    def test_movie_only_sources_do_not_appear_as_series(self):
        names = {scraper.name for scraper in self.registry.get_scrapers('tvshow')}
        self.assertNotIn('hdfilme', names)
        self.assertNotIn('streamkiste', names)


if __name__ == '__main__':
    unittest.main()
