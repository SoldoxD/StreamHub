"""Series provider selection and episode navigation, without Kodi or network."""
import importlib.util
import json
from pathlib import Path
import re
import sys
import types
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[1] / "src/plugin.video.streamhub/resources/streamhub/series.py"


class SeriesIntegrationTests(unittest.TestCase):
    def setUp(self):
        utils = types.ModuleType("streamhub.utils")
        utils.re, utils.json, utils.log = re, json, Mock()
        utils.format_exc = lambda: "fixture exception"
        with patch.dict(sys.modules, {"streamhub.utils": utils}):
            spec = importlib.util.spec_from_file_location("tested_series", SOURCE)
            self.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.module)
        self.source = types.SimpleNamespace(name="serienstream", virtual=False,
            get_seasons=Mock(return_value=[{"season_number": 1, "name": "Staffel 1"}]),
            get_episodes=Mock(return_value=[{"episode_number": 1, "name": "Pilot"},
                                           {"episode_number": 3, "name": "Third"}]))
        self.hit = {"provider": "serienstream", "url": "https://serienstream.to/serie/example"}
        self.show = {"title": "Example", "hits": [self.hit]}
        self.module._enabled_providers = Mock(return_value=[self.source])

    def test_series_only_provider_supplies_seasons_and_retains_choice(self):
        hit, seasons = self.module._season_metadata(self.show)
        self.assertIs(hit, self.hit)
        self.assertEqual(seasons[0]["season_number"], 1)
        self.assertEqual(self.show["_metadata_provider"], "serienstream")
        self.assertIs(self.module._meta_hit(self.show)[1], self.source)

    def test_disabled_provider_is_not_used_from_saved_search_hit(self):
        self.module._enabled_providers.return_value = []
        self.assertEqual(self.module._meta_hit(self.show), (None, None))
        self.source.get_seasons.assert_not_called()

    def test_failed_legacy_metadata_falls_back_to_new_provider(self):
        legacy = types.SimpleNamespace(name="vavoo", get_seasons=Mock(return_value=[]))
        self.module._META_PROVIDERS = (("vavoo", lambda: legacy),)
        self.module._enabled_providers.return_value = [legacy, self.source]
        self.show["hits"].insert(0, {"provider": "vavoo", "tmdb_id": "12"})
        self.assertIs(self.module._season_metadata(self.show)[0], self.hit)
        legacy.get_seasons.assert_called_once()
        self.assertIs(self.module._meta_hit(self.show)[1], self.source)

    def test_selected_provider_remains_first_even_with_legacy_hit(self):
        legacy = types.SimpleNamespace(name="vavoo", get_seasons=Mock())
        self.module._META_PROVIDERS = (("vavoo", lambda: legacy),)
        self.module._enabled_providers.return_value = [legacy, self.source]
        self.show["hits"].insert(0, {"provider": "vavoo"})
        self.show["_metadata_provider"] = "serienstream"
        self.assertIs(self.module._meta_hit(self.show)[1], self.source)

    def test_metadata_cancellation_is_propagated(self):
        class Cancelled(Exception):
            pass
        self.source.get_seasons.side_effect = Cancelled()
        with patch.dict(sys.modules, {"lib.scrapers.base": types.SimpleNamespace(Cancelled=Cancelled)}):
            with self.assertRaises(Cancelled):
                self.module._season_metadata(self.show)

    def test_real_episode_numbers_are_sorted_deduplicated_and_validated(self):
        self.source.get_episodes.return_value = [
            {"episode_number": 3}, {"episode_number": "1", "name": "Pilot"},
            {"episode_number": 3}, {"episode_number": -1}, {"episode_number": "bad"}, None]
        rows = self.module._episode_records(self.source, self.hit, 1)
        self.assertEqual([r["episode_number"] for r in rows], [1, 3])
        self.assertEqual(rows[0]["name"], "Pilot")

    def test_legacy_count_provider_still_lists_contiguous_episodes(self):
        provider = types.SimpleNamespace(get_episode_count=Mock(return_value=3))
        rows = self.module._episode_records(provider, {}, "1")
        self.assertEqual([r["episode_number"] for r in rows], [1, 2, 3])

    def test_kodi_navigation_does_not_invent_missing_episodes_or_autoplay_them(self):
        for name in ("ListItem", "ListItemInfoTag", "set_content", "set_category", "add", "end"):
            setattr(self.module, name, Mock())
        self.module._list_episodes(self.show, self.hit, "1")
        calls = self.module.add.call_args_list
        self.assertEqual([c.args[0]["episode"] for c in calls], ["1", "3"])
        self.assertEqual([c.args[0]["ep_count"] for c in calls], ["3", "3"])
        self.assertEqual(json.loads(calls[0].args[0]["episode_numbers"]), [1, 3])
        labels = [call.args[0] for call in self.module.ListItem.call_args_list]
        self.assertEqual(labels, ["S01E01 - Pilot", "S01E03 - Third"])
        self.module.end.assert_called_once()


if __name__ == "__main__":
    unittest.main()
