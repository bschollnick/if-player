"""The local trust/settings JSON store."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from unittest import TestCase

from if_player.settings_store import (
    get_reader_prefs,
    is_folder_trusted,
    is_strict_externals,
    load_settings,
    mark_folder_trusted,
    save_settings,
    set_reader_prefs,
    set_strict_externals,
)


class SettingsStoreTests(TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.settings_path = self.tmp / "settings.json"
        self.game_dir = self.tmp / "some_game"
        self.game_dir.mkdir()

    def test_loading_a_missing_settings_file_returns_empty_dict(self):
        self.assertEqual(load_settings(self.settings_path), {})

    def test_loading_a_corrupted_settings_file_returns_empty_dict_not_a_crash(self):
        self.settings_path.write_text("not valid json {{{", encoding="utf-8")
        self.assertEqual(load_settings(self.settings_path), {})

    def test_a_folder_is_untrusted_by_default(self):
        self.assertFalse(is_folder_trusted({}, self.game_dir))

    def test_marking_a_folder_trusted_then_checking_it_returns_true(self):
        settings = mark_folder_trusted({}, self.game_dir)
        self.assertTrue(is_folder_trusted(settings, self.game_dir))

    def test_marking_trusted_does_not_mutate_the_input_dict(self):
        original: dict[str, object] = {}
        mark_folder_trusted(original, self.game_dir)
        self.assertEqual(original, {})

    def test_marking_the_same_folder_trusted_twice_does_not_duplicate_it(self):
        settings = mark_folder_trusted({}, self.game_dir)
        settings = mark_folder_trusted(settings, self.game_dir)
        self.assertEqual(len(settings["trusted_folders"]), 1)

    def test_a_different_folder_is_unaffected_by_another_folders_trust(self):
        other_dir = self.tmp / "other_game"
        other_dir.mkdir()
        settings = mark_folder_trusted({}, self.game_dir)
        self.assertFalse(is_folder_trusted(settings, other_dir))

    def test_saving_then_loading_round_trips(self):
        settings = mark_folder_trusted({}, self.game_dir)
        save_settings(self.settings_path, settings)
        reloaded = load_settings(self.settings_path)
        self.assertTrue(is_folder_trusted(reloaded, self.game_dir))

    def test_saving_creates_parent_directories(self):
        nested_path = self.tmp / "nested" / "dir" / "settings.json"
        save_settings(nested_path, {})
        self.assertTrue(nested_path.is_file())


class StrictExternalsSettingTests(TestCase):
    """Off unless explicitly enabled -- a player meeting a game with one
    unwired binding should still be able to play it."""

    def test_it_is_off_when_nothing_is_saved(self):
        self.assertFalse(is_strict_externals({}))

    def test_enabling_then_reading_it_back(self):
        self.assertTrue(is_strict_externals(set_strict_externals({}, True)))

    def test_disabling_it_again(self):
        self.assertFalse(is_strict_externals(set_strict_externals(set_strict_externals({}, True), False)))

    def test_a_non_boolean_stored_value_reads_as_off(self):
        """A corrupted settings file must not silently turn strictness on."""
        self.assertFalse(is_strict_externals({"strict_externals": "yes"}))

    def test_setting_it_does_not_mutate_the_input(self):
        settings = {}
        set_strict_externals(settings, True)
        self.assertEqual(settings, {})


class ReaderPrefsTests(TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_defaults_are_medium_when_nothing_is_saved(self):
        prefs = get_reader_prefs({})
        self.assertEqual(prefs, {"font_size": "medium", "text_width": "medium"})

    def test_setting_valid_values_then_reading_them_back(self):
        settings = set_reader_prefs({}, "large", "narrow")
        prefs = get_reader_prefs(settings)
        self.assertEqual(prefs, {"font_size": "large", "text_width": "narrow"})

    def test_setting_does_not_mutate_the_input_dict(self):
        original: dict[str, object] = {}
        set_reader_prefs(original, "large", "narrow")
        self.assertEqual(original, {})

    def test_an_invalid_font_size_is_silently_ignored(self):
        settings = set_reader_prefs({}, "gigantic", "narrow")
        prefs = get_reader_prefs(settings)
        self.assertEqual(prefs["font_size"], "medium")
        self.assertEqual(prefs["text_width"], "narrow")

    def test_an_invalid_text_width_is_silently_ignored(self):
        settings = set_reader_prefs({}, "large", "gigantic")
        prefs = get_reader_prefs(settings)
        self.assertEqual(prefs["font_size"], "large")
        self.assertEqual(prefs["text_width"], "medium")

    def test_setting_one_field_does_not_reset_the_other_to_default(self):
        settings = set_reader_prefs({}, "large", "narrow")
        settings = set_reader_prefs(settings, "small", "gigantic")
        prefs = get_reader_prefs(settings)
        self.assertEqual(prefs, {"font_size": "small", "text_width": "narrow"})

    def test_a_corrupted_stored_value_falls_back_to_default_rather_than_surfacing(self):
        prefs = get_reader_prefs({"if_font_size": 42, "if_text_width": None})
        self.assertEqual(prefs, {"font_size": "medium", "text_width": "medium"})

    def test_saving_then_loading_from_disk_round_trips(self):
        settings_path = self.tmp / "settings.json"
        settings = set_reader_prefs({}, "small", "wide")
        save_settings(settings_path, settings)
        reloaded = load_settings(settings_path)
        self.assertEqual(get_reader_prefs(reloaded), {"font_size": "small", "text_width": "wide"})
