"""The per-turn play loop."""

from __future__ import annotations

from pathlib import Path
from unittest import TestCase

from if_session.session_state import SAVE_FORMAT_VERSION, SaveFormatError
from ink_engine.game_folder import GameFolderError
from ink_engine.plugin import Plugin

from if_player.game_session import (
    MAX_TRANSCRIPT_TURNS,
    append_transcript_entry,
    bindings_for,
    build_saved_state,
    choose,
    open_game,
    restart,
    undo,
)

FIXTURES = Path(__file__).parent / "fixtures"
SIMPLE_GAME = FIXTURES / "simple_game"


class OpenGameTests(TestCase):
    """Opening a game folder, both fresh and resumed from a save."""

    def test_a_fresh_game_starts_with_the_opening_turn_already_run(self):
        session = open_game(SIMPLE_GAME, saved_state=None, plugins={}, active_plugin_names=[], trusted=False)
        self.assertIn("Hello, traveler.", session.state.last_turn_text)
        self.assertEqual(len(session.state.current_choices), 2)

    def test_a_fresh_game_has_no_undo_target(self):
        session = open_game(SIMPLE_GAME, saved_state=None, plugins={}, active_plugin_names=[], trusted=False)
        self.assertIsNone(session.previous_state)

    def test_resuming_from_a_saved_state_restores_the_same_turn(self):
        original = open_game(SIMPLE_GAME, saved_state=None, plugins={}, active_plugin_names=[], trusted=False)
        saved = build_saved_state(original.state, original.previous_state, original.transcript, original.engine_state)

        resumed = open_game(SIMPLE_GAME, saved_state=saved, plugins={}, active_plugin_names=[], trusted=False)
        self.assertEqual(resumed.state.turn_count, original.state.turn_count)
        self.assertEqual(resumed.state.last_turn_text, original.state.last_turn_text)
        self.assertEqual(len(resumed.state.current_choices), len(original.state.current_choices))

    def test_resuming_from_a_newer_save_format_is_refused(self):
        """`from_dict()` reads every field with a default, so an
        unrecognised envelope would otherwise load as defaulted data
        rather than an error."""
        original = open_game(SIMPLE_GAME, saved_state=None, plugins={}, active_plugin_names=[], trusted=False)
        saved = build_saved_state(original.state, original.previous_state, original.transcript, original.engine_state)
        saved["save_format_version"] = SAVE_FORMAT_VERSION + 1

        with self.assertRaises(SaveFormatError) as caught:
            open_game(SIMPLE_GAME, saved_state=saved, plugins={}, active_plugin_names=[], trusted=False)
        self.assertIn("newer version", str(caught.exception))

    def test_a_save_written_before_versioning_still_resumes(self):
        original = open_game(SIMPLE_GAME, saved_state=None, plugins={}, active_plugin_names=[], trusted=False)
        saved = build_saved_state(original.state, original.previous_state, original.transcript, original.engine_state)
        del saved["save_format_version"]

        resumed = open_game(SIMPLE_GAME, saved_state=saved, plugins={}, active_plugin_names=[], trusted=False)
        self.assertEqual(resumed.state.turn_count, original.state.turn_count)

    def test_a_missing_compiled_story_raises(self):
        empty_dir = FIXTURES / "no_such_game"
        with self.assertRaises(GameFolderError):
            open_game(empty_dir, saved_state=None, plugins={}, active_plugin_names=[], trusted=False)


class ChooseTests(TestCase):
    """Applying a choice and advancing the story."""

    def setUp(self):
        self.session = open_game(SIMPLE_GAME, saved_state=None, plugins={}, active_plugin_names=[], trusted=False)

    def test_choosing_advances_the_story(self):
        context = choose(self.session, 0, self.session.state.turn_count)
        self.assertIn("You went north.", context["text"])

    def test_choosing_increments_turn_count(self):
        before = self.session.state.turn_count
        choose(self.session, 0, before)
        self.assertEqual(self.session.state.turn_count, before + 1)

    def test_a_stale_turn_count_is_rejected_without_applying_the_choice(self):
        real_turn_count = self.session.state.turn_count
        context = choose(self.session, 0, real_turn_count - 1)
        self.assertEqual(context, {"stale": True})
        # The choice must NOT have been applied.
        self.assertEqual(self.session.state.turn_count, real_turn_count)

    def test_choosing_records_a_transcript_entry_with_the_chosen_label(self):
        self.assertEqual(self.session.transcript, [])
        choose(self.session, 0, self.session.state.turn_count)
        self.assertEqual(len(self.session.transcript), 1)
        self.assertEqual(self.session.transcript[0]["chosen_label"], "Go north")

    def test_choosing_sets_a_real_undo_target(self):
        self.assertIsNone(self.session.previous_state)
        choose(self.session, 0, self.session.state.turn_count)
        self.assertIsNotNone(self.session.previous_state)

    def test_the_context_reports_can_undo_correctly(self):
        context = choose(self.session, 0, self.session.state.turn_count)
        self.assertTrue(context["can_undo"])

    def test_out_of_range_choice_index_raises(self):
        with self.assertRaises(IndexError):
            choose(self.session, 99, self.session.state.turn_count)

    def test_the_story_reports_done_once_no_choices_remain(self):
        """This fixture's own "north" branch ends the story in one turn
        -- no further choices exist after choosing it."""
        context = choose(self.session, 0, self.session.state.turn_count)
        self.assertTrue(context["done"])
        self.assertEqual(context["choices"], [])


class UndoTests(TestCase):
    """Restoring the one-level undo snapshot."""

    def setUp(self):
        self.session = open_game(SIMPLE_GAME, saved_state=None, plugins={}, active_plugin_names=[], trusted=False)

    def test_undo_with_nothing_to_undo_returns_none(self):
        self.assertIsNone(undo(self.session))

    def test_undo_restores_the_prior_turns_own_text(self):
        opening_text = self.session.state.last_turn_text
        choose(self.session, 0, self.session.state.turn_count)
        self.assertNotEqual(self.session.state.last_turn_text, opening_text)

        context = undo(self.session)
        self.assertEqual(context["text"], opening_text)

    def test_undo_restores_the_prior_turn_count(self):
        original_turn_count = self.session.state.turn_count
        choose(self.session, 0, self.session.state.turn_count)
        undo(self.session)
        self.assertEqual(self.session.state.turn_count, original_turn_count)

    def test_undo_clears_can_undo_when_there_was_only_one_turn_to_undo(self):
        choose(self.session, 0, self.session.state.turn_count)
        context = undo(self.session)
        self.assertFalse(context["can_undo"])

    def test_undoing_the_very_first_turn_leaves_nothing_further_to_undo(self):
        """The restored snapshot from the story's OPENING turn carries no
        earlier previous_state of its own -- undoing once more (after
        already undoing the one real choice this fixture supports) must
        report nothing left to undo, not raise or loop."""
        choose(self.session, 0, self.session.state.turn_count)
        self.assertIsNotNone(self.session.previous_state)
        undo(self.session)
        self.assertIsNone(undo(self.session))


class RestartTests(TestCase):
    """Discarding any saved state and starting over."""

    def test_restart_produces_a_fresh_session_regardless_of_prior_progress(self):
        session = open_game(SIMPLE_GAME, saved_state=None, plugins={}, active_plugin_names=[], trusted=False)
        # turn_count starts at -1 (InkRuntimeState's own documented
        # default) and only choose() increments it -- confirm real
        # progress was made before checking restart discards it.
        opening_turn_count = session.state.turn_count
        choose(session, 0, opening_turn_count)
        self.assertGreater(session.state.turn_count, opening_turn_count)
        self.assertNotEqual(session.transcript, [])

        fresh = restart(SIMPLE_GAME, plugins={}, active_plugin_names=[], trusted=False)
        self.assertEqual(fresh.transcript, [])
        self.assertIsNone(fresh.previous_state)
        self.assertEqual(fresh.state.turn_count, opening_turn_count)


class BindingsForTests(TestCase):
    """The trust gate: the one function allowed to branch on `trusted`."""

    def test_an_untrusted_session_gets_no_bindings_even_with_declared_plugins(self):
        plugin = Plugin(name="test_plugin", display_name="Test", bindings={"is_day_now": lambda: True})
        result = bindings_for({"test_plugin": plugin}, ["test_plugin"], {}, trusted=False)
        self.assertEqual(result, {})

    def test_a_trusted_session_with_a_declared_plugin_gets_its_bindings(self):
        plugin = Plugin(name="test_plugin", display_name="Test", bindings={"is_day_now": lambda: True})
        result = bindings_for({"test_plugin": plugin}, ["test_plugin"], {}, trusted=True)
        self.assertIn("is_day_now", result)

    def test_a_trusted_session_naming_an_undiscovered_plugin_is_silently_skipped(self):
        """A game's own manifest may name a plugin that failed to
        discover (e.g. a typo) -- this must not raise."""
        result = bindings_for({}, ["nonexistent_plugin"], {}, trusted=True)
        self.assertEqual(result, {})


class InitialGlobalsTests(TestCase):
    """A fresh game may be seeded with Ink VAR values before its opening
    turn -- the character-creation seam. `simple_game` declares no VARs of
    its own, so these check the plumbing reaches `state.globals`; the
    seeding SEMANTICS (a seeded value driving the story's own arithmetic
    and branches) are covered against a real VAR story in ink_engine's own
    `test_engine_start_new_story.py`."""

    def test_a_seeded_global_reaches_the_running_state(self):
        session = open_game(
            SIMPLE_GAME,
            saved_state=None,
            plugins={},
            active_plugin_names=[],
            trusted=False,
            initial_globals={"player_name": "Ada"},
        )
        self.assertEqual(session.state.globals["player_name"], "Ada")

    def test_seeding_is_optional(self):
        session = open_game(SIMPLE_GAME, saved_state=None, plugins={}, active_plugin_names=[], trusted=False)
        self.assertEqual(session.state.globals, {})

    def test_restart_can_reseed(self):
        session = restart(SIMPLE_GAME, {}, [], False, initial_globals={"player_name": "Grace"})
        self.assertEqual(session.state.globals["player_name"], "Grace")

    def test_a_resumed_save_ignores_seeding(self):
        """A resumed game already carries its own globals -- re-seeding it
        would silently overwrite real progress."""
        original = open_game(
            SIMPLE_GAME,
            saved_state=None,
            plugins={},
            active_plugin_names=[],
            trusted=False,
            initial_globals={"player_name": "Ada"},
        )
        saved = build_saved_state(original.state, original.previous_state, original.transcript, original.engine_state)
        resumed = open_game(
            SIMPLE_GAME,
            saved_state=saved,
            plugins={},
            active_plugin_names=[],
            trusted=False,
            initial_globals={"player_name": "Overwritten"},
        )
        self.assertEqual(resumed.state.globals["player_name"], "Ada")


class StrictExternalsTests(TestCase):
    """Strict mode is a live host setting: it is threaded onto the session
    so every later rebuild (undo, a panel hook) keeps the mode the game was
    opened with, and never travels in a save file."""

    def test_a_session_is_lenient_by_default(self):
        session = open_game(SIMPLE_GAME, saved_state=None, plugins={}, active_plugin_names=[], trusted=False)
        self.assertFalse(session.strict_externals)
        self.assertFalse(session.state.strict_externals)

    def test_strict_mode_reaches_the_running_state(self):
        session = open_game(SIMPLE_GAME, saved_state=None, plugins={}, active_plugin_names=[], trusted=False, strict_externals=True)
        self.assertTrue(session.state.strict_externals)

    def test_a_resumed_session_is_as_strict_as_it_is_told(self):
        original = open_game(SIMPLE_GAME, saved_state=None, plugins={}, active_plugin_names=[], trusted=False)
        saved = build_saved_state(original.state, original.previous_state, original.transcript, original.engine_state)
        resumed = open_game(SIMPLE_GAME, saved_state=saved, plugins={}, active_plugin_names=[], trusted=False, strict_externals=True)
        self.assertTrue(resumed.state.strict_externals)

    def test_strictness_is_not_written_into_a_save(self):
        session = open_game(SIMPLE_GAME, saved_state=None, plugins={}, active_plugin_names=[], trusted=False, strict_externals=True)
        saved = build_saved_state(session.state, session.previous_state, session.transcript, session.engine_state)
        self.assertNotIn("strict_externals", saved)

    def test_undo_rebuilds_the_state_with_the_same_strictness(self):
        session = open_game(SIMPLE_GAME, saved_state=None, plugins={}, active_plugin_names=[], trusted=False, strict_externals=True)
        choose(session, 0, session.state.turn_count)
        undo(session)
        self.assertTrue(session.state.strict_externals)

    def test_restart_keeps_the_mode(self):
        session = restart(SIMPLE_GAME, {}, [], False, strict_externals=True)
        self.assertTrue(session.state.strict_externals)


class BuildSavedStateTests(TestCase):
    def test_the_saved_dict_carries_all_three_bookkeeping_keys(self):
        session = open_game(SIMPLE_GAME, saved_state=None, plugins={}, active_plugin_names=[], trusted=False)
        saved = build_saved_state(session.state, session.previous_state, session.transcript, session.engine_state)
        self.assertIn("transcript", saved)
        self.assertIn("previous_state", saved)
        self.assertIn("engine_state", saved)
        # And InkRuntimeState.to_dict()'s own fields are still present.
        self.assertIn("turn_count", saved)
        self.assertIn("pointer", saved)


class AppendTranscriptEntryTests(TestCase):
    def test_an_entry_is_appended(self):
        result = append_transcript_entry([], "Some text", "A choice")
        self.assertEqual(result, [{"text": "Some text", "chosen_label": "A choice"}])

    def test_the_opening_turn_has_no_chosen_label(self):
        result = append_transcript_entry([], "Opening text", None)
        self.assertIsNone(result[0]["chosen_label"])

    def test_the_transcript_is_capped_dropping_the_oldest_entries(self):
        transcript: list[dict[str, object]] = []
        for i in range(MAX_TRANSCRIPT_TURNS + 5):
            transcript = append_transcript_entry(transcript, f"turn {i}", None)
        self.assertEqual(len(transcript), MAX_TRANSCRIPT_TURNS)
        self.assertEqual(transcript[0]["text"], "turn 5")
        self.assertEqual(transcript[-1]["text"], f"turn {MAX_TRANSCRIPT_TURNS + 4}")

    def test_appending_does_not_mutate_the_original_list(self):
        original: list[dict[str, object]] = []
        append_transcript_entry(original, "text", None)
        self.assertEqual(original, [])


class ActivePluginNamesSurviveRebindingTests(TestCase):
    """A session rebinds from the names it activated, never from its
    state keys.

    Reproduces the real shape that made this matter: a game's own plugin
    shares a generic plugin's `state_key` on purpose, so both operate on
    one store. Deriving active names from `engine_state.keys()` then
    yields the GENERIC plugin's name and rebinds it in place of the
    game's own -- silently, because both answer the same Ink function
    names. In the real game that swap covers over a thousand call sites.
    """

    GENERIC_NAME = "character_occupancy"
    GAME_NAME = "game_occupancy"
    SHARED_SLOT = "character_occupancy"

    def _plugins(self) -> dict[str, Plugin]:
        generic = Plugin(
            name=self.GENERIC_NAME,
            display_name="Generic occupancy",
            state_key=self.SHARED_SLOT,
            init_state=lambda config: {},
            bind=lambda own_state, engine_state, list_defs: {"where_is_now": lambda: "generic"},
        )
        # Same slot, different plugin name -- the arrangement a game
        # uses when it extends an engine plugin under its own name.
        game = Plugin(
            name=self.GAME_NAME,
            display_name="Game occupancy",
            state_key=self.SHARED_SLOT,
            init_state=lambda config: {},
            bind=lambda own_state, engine_state, list_defs: {"where_is_now": lambda: "game"},
        )
        return {self.GENERIC_NAME: generic, self.GAME_NAME: game}

    def _open(self):
        return open_game(
            SIMPLE_GAME,
            saved_state=None,
            plugins=self._plugins(),
            active_plugin_names=[self.GAME_NAME],
            trusted=True,
        )

    def test_the_session_remembers_the_names_it_activated(self):
        session = self._open()
        self.assertEqual(session.active_plugin_names, [self.GAME_NAME])

    def test_the_state_key_does_not_match_the_plugin_name(self):
        """Guards the premise: if these ever coincided the tests below
        would pass for the wrong reason."""
        session = self._open()
        self.assertIn(self.SHARED_SLOT, session.engine_state)
        self.assertNotIn(self.GAME_NAME, session.engine_state)

    def test_undo_rebinds_the_games_plugin_not_the_generic_one(self):
        session = self._open()
        choose(session, 0, session.state.turn_count)
        undo(session)
        self.assertEqual(session.state.engine_bindings["where_is_now"](), "game")

    def test_a_bindings_only_plugin_survives_undo(self):
        """A plugin with no `state_key` leaves no trace in
        `engine_state`, so a state-key-derived list drops it entirely."""
        plugins = self._plugins()
        plugins["extras"] = Plugin(name="extras", display_name="Extras", bindings={"extra_now": lambda: 1})
        session = open_game(
            SIMPLE_GAME,
            saved_state=None,
            plugins=plugins,
            active_plugin_names=[self.GAME_NAME, "extras"],
            trusted=True,
        )
        choose(session, 0, session.state.turn_count)
        undo(session)
        self.assertIn("extra_now", session.state.engine_bindings)
