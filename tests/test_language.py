"""Language evidence used to prevent automatic host changes into other audio."""
import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / "src/plugin.video.streamhub/resources/streamhub/language.py"
spec = importlib.util.spec_from_file_location("streamhub.language", PATH)
language = importlib.util.module_from_spec(spec)
spec.loader.exec_module(language)


class LanguageNormalizationTests(unittest.TestCase):
    def test_kodi_iso_and_localized_language_names(self):
        cases = {
            "de": ("de", "DE", "deu", "ger", "German", "Deutsch", "de-DE", "de_AT"),
            "en": ("en", "eng", "English", "Englisch", "en-US"),
            "fr": ("fr", "fra", "fre", "Français", "Französisch"),
            "it": ("it", "ita", "Italiano", "Italienisch"),
            "es": ("es", "spa", "Español", "Spanisch", "es-419"),
            "tr": ("tr", "tur", "Türkçe", "Türkisch"),
            "zh": ("zho", "chi", "zh-Hant-TW"),
        }
        for expected, labels in cases.items():
            for label in labels:
                with self.subTest(label=label):
                    self.assertEqual(language.normalize_language(label), expected)

    def test_unknown_and_subtitle_labels_are_not_audio_languages(self):
        for label in (None, True, 1, {}, "", "any", "und", "OV", "OMU", "Dual", "Multi",
                      "DE-SUB", "GER SUB", "German subtitles", "de-abc", "x" * 257):
            with self.subTest(label=str(label)[:50]):
                self.assertEqual(language.normalize_language(label), "")


class ProviderLanguageTests(unittest.TestCase):
    def test_explicit_multilanguage_labels(self):
        for label in ("DE/EN", "Deutsch, Englisch", "German + English", "DE & EN",
                      "DE und EN", "DE;EN", "DE|EN", "Dual (DE/EN)"):
            with self.subTest(label=label):
                self.assertEqual(language.provider_audio_languages(label), {"de", "en"})

    def test_subtitle_languages_never_count_as_audio(self):
        for label in ("DE-SUB", "GER SUB", "German subtitles", "Deutsch Untertitel", "DE OMU"):
            with self.subTest(label=label):
                self.assertEqual(language.provider_audio_languages(label), set())
                self.assertFalse(language.provider_matches_language(label, "de"))

    def test_mixed_audio_and_subtitle_labels_keep_only_audio(self):
        for label in ("EN / DE-SUB", "English audio, German subtitles", "English with German subtitles"):
            with self.subTest(label=label):
                self.assertEqual(language.provider_audio_languages(label), {"en"})

    def test_unspecified_dual_multi_original_do_not_guess_german(self):
        for label in ("OV", "Original", "Dual", "Dual Audio", "Multi", "MULTI LANGUAGE", "DE-SUB/OV"):
            with self.subTest(label=label):
                self.assertFalse(language.provider_matches_language(label, "de"))

    def test_mapping_reads_only_language_fields(self):
        self.assertEqual(language.provider_audio_languages({"lang": "DE"}), {"de"})
        self.assertEqual(language.provider_audio_languages({"language": "eng"}), {"en"})
        self.assertEqual(language.provider_audio_languages({"languages": ["ger", "eng"]}), {"de", "en"})
        source = {"title": "German", "label": "DE", "host": "German", "url": "https://de.example/DE"}
        self.assertEqual(language.provider_audio_languages(source), set())

    def test_any_preference_does_not_require_german(self):
        for want in (None, "", "ANY", "any", "*"):
            for source in ({}, {"lang": "FR"}, {"lang": "DE-SUB"}):
                with self.subTest(want=want, source=source):
                    self.assertTrue(language.provider_matches_language(source, want))
        self.assertFalse(language.provider_matches_language("DE", "unrecognized"))
        self.assertFalse(language.provider_matches_language("DE", "x" * 257))

    def test_selected_language_requires_explicit_match(self):
        self.assertTrue(language.provider_matches_language({"lang": "DE/EN"}, "eng"))
        self.assertFalse(language.provider_matches_language({"lang": "DE"}, "en"))
        self.assertFalse(language.provider_matches_language({}, "de"))

    def test_malformed_metadata_is_bounded(self):
        for source in (None, 1, {}, {"lang": {}}, [["de"]], "x" * 257):
            with self.subTest(source=str(source)[:50]):
                self.assertEqual(language.provider_audio_languages(source), set())
        self.assertEqual(language.provider_audio_languages(["en"] * 16 + ["de"]), {"en"})


class AudioStreamLanguageTests(unittest.TestCase):
    def test_selects_actual_audio_index_and_reports_explicit_languages(self):
        streams = [{"index": 0, "language": "eng", "name": "AAC 2.0"},
                   {"index": 3, "language": "ger", "name": "AC3 5.1"},
                   {"index": 4, "language": "und", "name": "Unknown"}]
        self.assertEqual(language.matching_audio_stream(streams, "de"), 3)
        self.assertEqual(language.matching_audio_stream(streams, "en"), 0)
        self.assertIsNone(language.matching_audio_stream(streams, "fr"))
        self.assertEqual(language.audio_stream_languages(streams), {"de", "en"})

    def test_track_language_is_authoritative_over_conflicting_name(self):
        streams = [{"index": 0, "language": "eng", "name": "German"}]
        self.assertEqual(language.audio_stream_languages(streams), {"en"})
        self.assertIsNone(language.matching_audio_stream(streams, "de"))

    def test_explicit_track_names_with_audio_metadata_are_accepted(self):
        for name in ("Deutsch", "German (AC3 5.1)", "de-DE AAC stereo", "Deutsch: DTS-HD 5.1"):
            with self.subTest(name=name):
                streams = [{"index": 2, "language": "und", "name": name}]
                self.assertEqual(language.matching_audio_stream(streams, "de"), 2)

    def test_arbitrary_names_and_subtitles_are_not_language_proof(self):
        for name in ("The German movie", "German Movie 2026", "DE-SUB", "German subtitles", "Dual", "Multi"):
            with self.subTest(name=name):
                streams = [{"index": 0, "language": "und", "name": name}]
                self.assertEqual(language.audio_stream_languages(streams), set())
                self.assertIsNone(language.matching_audio_stream(streams, "de"))

    def test_any_keeps_current_audio_track(self):
        for want in (None, "", "any"):
            self.assertIsNone(language.matching_audio_stream([{"index": 0, "language": "de"}], want))

    def test_invalid_indices_never_become_player_commands(self):
        for index in (-1, 256, True, 0.0, "0", None):
            with self.subTest(index=index):
                self.assertIsNone(language.matching_audio_stream([{"index": index, "language": "de"}], "de"))

    def test_malformed_stream_lists_are_safe_and_bounded(self):
        for streams in (None, {}, "[]", [None, "de", 1]):
            with self.subTest(streams=streams):
                self.assertEqual(language.audio_stream_languages(streams), set())
                self.assertIsNone(language.matching_audio_stream(streams, "de"))
        streams = [{"index": 0, "language": "en"}] * 256 + [{"index": 1, "language": "de"}]
        self.assertIsNone(language.matching_audio_stream(streams, "de"))


if __name__ == "__main__":
    unittest.main()
