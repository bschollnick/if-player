"""The PlayerAPI js_api object itself -- a thin JSON-in/JSON-out adapter
over game_session.py, exercised here exactly the way pywebview's JS
bridge would call it (no real window; PlayerAPI has no pywebview import
at all)."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from unittest import TestCase

from if_player.player_api import PlayerAPI

FIXTURES = Path(__file__).parent / "fixtures"
SIMPLE_GAME = FIXTURES / "simple_game"


class PlayerAPITestCase(TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.api = PlayerAPI(settings_path=self.tmp / "settings.json")


class OpenGameTests(PlayerAPITestCase):
    def test_opening_an_untrusted_game_with_no_required_plugins_plays_immediately(self):
        context = self.api.open_game(str(SIMPLE_GAME))
        self.assertFalse(context["needs_trust"])
        self.assertIn("Hello, traveler.", context["text"])

    def test_opening_returns_the_play_layout(self):
        context = self.api.open_game(str(SIMPLE_GAME))
        self.assertEqual(context["play_layout"], "classic")

    def test_opening_returns_default_reader_prefs(self):
        context = self.api.open_game(str(SIMPLE_GAME))
        self.assertEqual(context["reader_prefs"], {"font_size": "medium", "text_width": "medium"})

    def test_opening_returns_previously_saved_reader_prefs(self):
        self.api.set_reader_prefs("large", "wide")
        context = self.api.open_game(str(SIMPLE_GAME))
        self.assertEqual(context["reader_prefs"], {"font_size": "large", "text_width": "wide"})

    def test_opening_a_missing_story_returns_an_error(self):
        empty_dir = self.tmp / "empty_game"
        empty_dir.mkdir()
        context = self.api.open_game(str(empty_dir))
        self.assertIn("error", context)

    def test_opening_writes_a_save_file(self):
        self.api.open_game(str(SIMPLE_GAME))
        saves_dir = self.tmp / "saves"
        self.assertTrue(any(saves_dir.iterdir()))

    def test_reopening_resumes_from_the_save_file(self):
        self.api.open_game(str(SIMPLE_GAME))
        self.api.choose(0, self.api.session.state.turn_count)

        fresh_api = PlayerAPI(settings_path=self.tmp / "settings.json")
        context = fresh_api.open_game(str(SIMPLE_GAME))
        self.assertIn("You went north.", context["text"])

    def test_image_urls_are_converted_to_file_uris(self):
        context = self.api.open_game(str(SIMPLE_GAME))
        for path in context["image_urls"]:
            self.assertTrue(path.startswith("file://"))

    def test_a_game_with_no_panel_gets_a_none_panel(self):
        context = self.api.open_game(str(SIMPLE_GAME))
        self.assertIsNone(context["panel"])


class ChooseUndoRestartTests(PlayerAPITestCase):
    def setUp(self):
        super().setUp()
        self.api.open_game(str(SIMPLE_GAME))

    def test_choosing_advances_the_story(self):
        context = self.api.choose(0, self.api.session.state.turn_count)
        self.assertIn("You went north.", context["text"])

    def test_choosing_with_a_stale_turn_count_is_rejected(self):
        real_turn_count = self.api.session.state.turn_count
        context = self.api.choose(0, real_turn_count - 1)
        self.assertEqual(context, {"stale": True})

    def test_choosing_an_out_of_range_index_returns_an_error_not_a_crash(self):
        context = self.api.choose(99, self.api.session.state.turn_count)
        self.assertIn("error", context)

    def test_undo_restores_the_opening_text(self):
        opening_text = self.api.session.state.last_turn_text
        self.api.choose(0, self.api.session.state.turn_count)
        context = self.api.undo()
        self.assertEqual(context["text"], opening_text)

    def test_undo_with_nothing_to_undo_returns_an_error(self):
        context = self.api.undo()
        self.assertIn("error", context)

    def test_restart_discards_prior_progress(self):
        self.api.choose(0, self.api.session.state.turn_count)
        context = self.api.restart()
        self.assertEqual(context["transcript"], [])

    def test_calling_choose_before_any_game_is_open_returns_an_error(self):
        fresh_api = PlayerAPI(settings_path=self.tmp / "settings.json")
        context = fresh_api.choose(0, 0)
        self.assertIn("error", context)

    def test_calling_undo_before_any_game_is_open_returns_an_error(self):
        fresh_api = PlayerAPI(settings_path=self.tmp / "settings.json")
        context = fresh_api.undo()
        self.assertIn("error", context)

    def test_calling_restart_before_any_game_is_open_returns_an_error(self):
        fresh_api = PlayerAPI(settings_path=self.tmp / "settings.json")
        context = fresh_api.restart()
        self.assertIn("error", context)


class TrustTests(PlayerAPITestCase):
    def test_a_folder_is_untrusted_by_default(self):
        self.assertFalse(self.api.is_trusted(str(SIMPLE_GAME)))

    def test_confirming_trust_persists_across_a_fresh_api_instance(self):
        self.api.confirm_trust(str(SIMPLE_GAME))
        fresh_api = PlayerAPI(settings_path=self.tmp / "settings.json")
        self.assertTrue(fresh_api.is_trusted(str(SIMPLE_GAME)))

    def test_a_game_with_no_required_plugins_never_needs_a_trust_prompt(self):
        """SIMPLE_GAME's own manifest declares no plugins at all, so
        there's no game code to run and trust is irrelevant."""
        context = self.api.open_game(str(SIMPLE_GAME))
        self.assertFalse(context["needs_trust"])


class ReaderPrefsAPITests(PlayerAPITestCase):
    def test_defaults_are_medium(self):
        self.assertEqual(self.api.get_reader_prefs(), {"font_size": "medium", "text_width": "medium"})

    def test_setting_then_getting_returns_the_saved_values(self):
        self.api.set_reader_prefs("small", "narrow")
        self.assertEqual(self.api.get_reader_prefs(), {"font_size": "small", "text_width": "narrow"})

    def test_set_reader_prefs_returns_the_real_saved_values(self):
        result = self.api.set_reader_prefs("large", "wide")
        self.assertEqual(result, {"prefs": {"font_size": "large", "text_width": "wide"}})

    def test_an_invalid_value_is_ignored_rather_than_erroring(self):
        result = self.api.set_reader_prefs("gigantic", "narrow")
        self.assertEqual(result["prefs"]["font_size"], "medium")
        self.assertEqual(result["prefs"]["text_width"], "narrow")

    def test_prefs_persist_across_a_fresh_api_instance(self):
        self.api.set_reader_prefs("large", "narrow")
        fresh_api = PlayerAPI(settings_path=self.tmp / "settings.json")
        self.assertEqual(fresh_api.get_reader_prefs(), {"font_size": "large", "text_width": "narrow"})

    def test_prefs_are_app_wide_not_per_game(self):
        """Unlike trust (per-folder), reader prefs are one set of values
        shared across every game."""
        self.api.open_game(str(SIMPLE_GAME))
        self.api.set_reader_prefs("large", "narrow")
        other_game_dir = self.tmp / "another_game"
        other_game_dir.mkdir()
        self.assertEqual(self.api.get_reader_prefs(), {"font_size": "large", "text_width": "narrow"})


class PanelTests(PlayerAPITestCase):
    def setUp(self):
        super().setUp()
        self.api.open_game(str(SIMPLE_GAME))

    def test_panel_tab_on_a_game_with_no_panel_returns_empty_dict(self):
        self.assertEqual(self.api.panel_tab("inventory"), {})

    def test_panel_action_on_a_game_with_no_panel_returns_empty_detail(self):
        self.assertEqual(self.api.panel_action("examine", "sword"), {"detail_text": ""})

    def test_panel_command_with_a_stale_turn_count_is_rejected(self):
        real_turn_count = self.api.session.state.turn_count
        result = self.api.panel_command("use", "sword", real_turn_count - 1)
        self.assertEqual(result, {"stale": True})

    def test_panel_command_on_a_game_with_no_panel_returns_empty_detail(self):
        result = self.api.panel_command("use", "sword", self.api.session.state.turn_count)
        self.assertEqual(result.get("panel_detail"), "")


class FindCoverTests(PlayerAPITestCase):
    def test_a_game_with_no_cover_image_returns_none(self):
        self.assertIsNone(self.api.find_cover(str(SIMPLE_GAME)))
