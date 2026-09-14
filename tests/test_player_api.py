"""The PlayerAPI js_api object itself -- a thin JSON-in/JSON-out adapter
over game_session.py, exercised here exactly the way pywebview's JS
bridge would call it (no real window; PlayerAPI has no pywebview import
at all)."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from ink_engine.engine import UnboundExternalError

from if_player.player_api import PlayerAPI, _to_file_uri

FIXTURES = Path(__file__).parent / "fixtures"
SIMPLE_GAME = FIXTURES / "simple_game"
STYLED_GAME = FIXTURES / "styled_game"
CHOICE_IMAGE_GAME = FIXTURES / "choice_image_game"


class PlayerAPITestCase(TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.api = PlayerAPI(settings_path=self.tmp / "settings.json")


class GameMountTests(PlayerAPITestCase):
    """Opening a second game must not leave it running the first's code."""

    def _trusted_game(self, tag: str) -> Path:
        game = self.tmp / tag / "mygame"
        shutil.copytree(SIMPLE_GAME, game)
        (game / "__init__.py").write_text("", encoding="utf-8")
        (game / "plugins.py").write_text(
            f"from ink_engine.plugin import Plugin\nPLUGIN = Plugin(name='p_{tag}', display_name='{tag}')\n",
            encoding="utf-8",
        )
        self.api.confirm_trust(str(game))
        return game

    def _own_plugins(self) -> list[str]:
        return sorted(name for name in self.api.session.plugins if name.startswith("p_"))

    def test_a_second_game_gets_its_own_plugins(self):
        self.api.open_game(str(self._trusted_game("A")))
        self.assertEqual(self._own_plugins(), ["p_A"])
        self.api.open_game(str(self._trusted_game("B")))
        self.assertEqual(self._own_plugins(), ["p_B"])

    def test_the_session_holds_its_mount(self):
        self.api.open_game(str(self._trusted_game("A")))
        self.assertIsNotNone(self.api.session.mount)

    def test_opening_games_does_not_accumulate_path_entries(self):
        self.api.open_game(str(self._trusted_game("A")))
        after_first = len(sys.path)
        self.api.open_game(str(self._trusted_game("B")))
        self.assertEqual(len(sys.path), after_first)

    def test_an_untrusted_game_is_never_mounted(self):
        game = self.tmp / "untrusted" / "mygame"
        shutil.copytree(SIMPLE_GAME, game)
        (game / "__init__.py").write_text("", encoding="utf-8")
        self.api.open_game(str(game))
        self.assertIsNone(self.api.session.mount)


class ManifestSupportAPITests(PlayerAPITestCase):
    """A game this engine cannot play is refused with a message the
    player can act on, not an exception across the js_api boundary."""

    def _game_declaring(self, manifest: str) -> Path:
        game = self.tmp / "game"
        shutil.rmtree(game, ignore_errors=True)
        shutil.copytree(SIMPLE_GAME, game)
        (game / "manifest.yaml").write_text(manifest, encoding="utf-8")
        return game

    def test_a_future_manifest_version_is_refused(self):
        game = self._game_declaring("MANIFEST_VERSION: 99\nMAIN_STORY_FILE: story.inkj\n")
        result = self.api.open_game(str(game))
        self.assertIn("newer player", result.get("error", ""))

    def test_another_story_format_is_refused(self):
        game = self._game_declaring("ENGINE_FORMAT: twine\nMAIN_STORY_FILE: story.inkj\n")
        result = self.api.open_game(str(game))
        self.assertIn("twine", result.get("error", ""))

    def test_a_refused_game_does_not_become_the_open_session(self):
        """Refusing must leave no half-opened game behind."""
        self.api.open_game(str(self._game_declaring("ENGINE_FORMAT: twine\nMAIN_STORY_FILE: story.inkj\n")))
        self.assertIsNone(self.api.session)

    def test_a_supported_game_still_opens(self):
        game = self._game_declaring("MANIFEST_VERSION: 1\nENGINE_FORMAT: ink\nMAIN_STORY_FILE: story.inkj\n")
        self.assertNotIn("error", self.api.open_game(str(game)))


class JsApiNeverRaisesTests(PlayerAPITestCase):
    """Every js_api method returns a dict rather than raising.

    pywebview serializes a method's RETURN VALUE and resolves the JS
    promise with it; an exception rejects the promise instead
    (`webview/js/api.js` `_checkValue`). The shell's call sites are all
    `.then(...)` with no `.catch(...)`, so a rejection runs no handler at
    all -- the window simply stops responding, with no error shown.
    """

    def _raising(self):
        """Patch the engine calls a rebuilt session goes through."""
        return (
            patch("if_player.game_session.start_new_story", side_effect=UnboundExternalError("boom")),
            patch("if_player.game_session.load_game_state", side_effect=UnboundExternalError("boom")),
        )

    def test_open_game_reports_an_unbound_external(self):
        with patch("if_player.game_session.start_new_story", side_effect=UnboundExternalError("boom")):
            result = self.api.open_game(str(SIMPLE_GAME))
        self.assertIn("error", result)

    def test_restart_reports_an_unbound_external(self):
        self.api.open_game(str(SIMPLE_GAME))
        first, second = self._raising()
        with first, second:
            result = self.api.restart()
        self.assertIn("error", result)

    def test_quickload_reports_an_unbound_external(self):
        self.api.open_game(str(SIMPLE_GAME))
        self.api.quicksave()
        first, second = self._raising()
        with first, second:
            result = self.api.quickload()
        self.assertIn("error", result)

    def test_load_from_slot_reports_an_unbound_external(self):
        self.api.open_game(str(SIMPLE_GAME))
        self.api.save_to_slot(0, "label")
        first, second = self._raising()
        with first, second:
            result = self.api.load_from_slot(0)
        self.assertIn("error", result)

    def test_choose_reports_an_unbound_external(self):
        self.api.open_game(str(SIMPLE_GAME))
        with patch("if_player.game_session.choose", side_effect=UnboundExternalError("boom")):
            result = self.api.choose(0, self.api.session.state.turn_count)
        self.assertIn("error", result)


class StrictExternalsAPITests(PlayerAPITestCase):
    """The js_api surface for strict-externals mode."""

    def test_it_is_off_by_default(self):
        self.assertFalse(self.api.get_strict_externals())

    def test_enabling_it_persists_and_reads_back(self):
        self.assertEqual(self.api.set_strict_externals(True), {"strict_externals": True})
        self.assertTrue(self.api.get_strict_externals())

    def test_a_session_opened_afterwards_runs_strict(self):
        self.api.set_strict_externals(True)
        self.api.open_game(str(SIMPLE_GAME))
        self.assertTrue(self.api.session.state.strict_externals)

    def test_restart_keeps_strict_mode(self):
        """`_resume_from_state()` re-derives trust and plugins fresh; it
        must re-derive strictness too, or restart silently drops it."""
        self.api.set_strict_externals(True)
        self.api.open_game(str(SIMPLE_GAME))
        self.api.restart()
        self.assertTrue(self.api.session.state.strict_externals)

    def test_quickload_keeps_strict_mode(self):
        self.api.set_strict_externals(True)
        self.api.open_game(str(SIMPLE_GAME))
        self.api.quicksave()
        self.api.quickload()
        self.assertTrue(self.api.session.state.strict_externals)

    def test_a_live_session_keeps_the_mode_it_was_opened_with(self):
        """Toggling mid-play must not change how the open game behaves."""
        self.api.open_game(str(SIMPLE_GAME))
        self.api.set_strict_externals(True)
        self.assertFalse(self.api.session.state.strict_externals)


class ConstructorTests(TestCase):
    def test_omitting_settings_path_resolves_the_real_default_location(self):
        """The only production caller (main.py) constructs PlayerAPI()
        with no arguments -- this must not crash, and every test above
        deliberately never exercises this branch since they all pass an
        explicit settings_path to avoid touching the real per-user
        config directory."""
        api = PlayerAPI()
        self.assertTrue(str(api.settings_path).endswith("if_player/settings.json"))


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

    def test_opening_a_game_with_no_styles_css_returns_none(self):
        context = self.api.open_game(str(SIMPLE_GAME))
        self.assertIsNone(context["prose_styles"])

    def test_opening_a_game_with_its_own_styles_css_returns_its_text(self):
        context = self.api.open_game(str(STYLED_GAME))
        expected = (STYLED_GAME / "styles.css").read_text(encoding="utf-8")
        self.assertEqual(context["prose_styles"], expected)

    def test_styled_game_prose_carries_its_own_style_tags_verbatim(self):
        """open_game() returns raw story text; the shell (app.js's
        storyHtml()) is what turns <style=name> into a real span, not
        PlayerAPI -- so the tags must survive here untouched."""
        context = self.api.open_game(str(STYLED_GAME))
        self.assertIn("<style=computer>", context["text"])
        self.assertIn("<style=alice>", context["text"])

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

    def test_a_data_uri_is_left_alone(self):
        """A bundle resolves media to `data:` URIs, which carry the bytes
        already and are not paths; treating one as a path raised."""
        data_uri = "data:image/jpeg;base64,/9j/4AAQ"
        self.assertEqual(_to_file_uri(data_uri), data_uri)

    def test_an_absolute_path_still_becomes_a_file_uri(self):
        self.assertEqual(_to_file_uri("/games/art/x.jpg"), "file:///games/art/x.jpg")

    def test_a_choice_carries_its_own_image(self):
        """Standard Ink choice tags (`* [Text # image: x.jpg]`): the
        original picks a character's model by appearance this way."""
        context = self.api.open_game(str(CHOICE_IMAGE_GAME))
        first = context["choices"][0]
        self.assertEqual(first["text"], "As a cleaner")
        self.assertEqual(len(first["image_urls"]), 1)
        self.assertTrue(first["image_urls"][0].startswith("file://"))

    def test_an_untagged_choice_has_no_images(self):
        context = self.api.open_game(str(CHOICE_IMAGE_GAME))
        self.assertEqual(context["choices"][-1]["image_urls"], [])

    def test_a_choice_tag_is_kept_out_of_the_choice_text(self):
        context = self.api.open_game(str(CHOICE_IMAGE_GAME))
        for choice in context["choices"]:
            self.assertNotIn("image:", choice["text"])

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
