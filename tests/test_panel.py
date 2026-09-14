"""Real-dispatch tests for panel.py -- exercised against an actual
importable sidebar.py fixture rather than mocks, to verify the real
argument order/positions panel_context()/panel_action()/panel_command()
each call their game's own hook with."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path
from unittest import TestCase

from if_player import panel
from if_player.game_session import open_game

FIXTURES = Path(__file__).parent / "fixtures"
SIMPLE_GAME = FIXTURES / "simple_game"
GAME_WITH_SIDEBAR = FIXTURES / "game_with_sidebar"


class PanelDispatchTests(TestCase):
    def setUp(self):
        # Real importlib dispatch needs the fixture's own parent on
        # sys.path, exactly like plugin_sources.ensure_importable() does
        # for a trusted game folder.
        self.addCleanup(lambda: sys.path.remove(str(FIXTURES)) if str(FIXTURES) in sys.path else None)
        if str(FIXTURES) not in sys.path:
            sys.path.insert(0, str(FIXTURES))
        sys.modules.pop("game_with_sidebar", None)
        sys.modules.pop("game_with_sidebar.sidebar", None)
        self.addCleanup(sys.modules.pop, "game_with_sidebar", None)
        self.addCleanup(sys.modules.pop, "game_with_sidebar.sidebar", None)

        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

        # Real InkRuntimeState from the simple_game fixture; game_dir/
        # trusted are swapped to point at the sidebar fixture, since
        # panel.py never reads story content, only session.game_dir.name/
        # trusted/plugins/engine_state.
        base_session = open_game(SIMPLE_GAME, saved_state=None, plugins={}, active_plugin_names=[], trusted=False)
        base_session.game_dir = GAME_WITH_SIDEBAR
        base_session.trusted = True
        self.session = base_session

    def test_panel_context_receives_engine_state_globals_and_bindings(self):
        self.session.engine_state = {"inventory": {"gold": 5}}
        context = panel.panel_context(self.session, {"player_name": "Ren"})
        self.assertEqual(context["engine_state"], {"inventory": {"gold": 5}})
        self.assertEqual(context["globals"], {"player_name": "Ren"})

    def test_panel_action_passes_action_id_and_target_id_in_order(self):
        result = panel.panel_action(self.session, {}, "examine", "sword")
        self.assertEqual(result, "action:examine:sword")

    def test_panel_command_passes_command_id_and_target_id_in_order(self):
        result = panel.panel_command(self.session, {}, "use", "potion")
        self.assertEqual(result, "command:use:potion")

    def test_panel_command_mutates_engine_state_in_place(self):
        self.session.engine_state = {}
        panel.panel_command(self.session, {}, "use", "potion")
        self.assertEqual(self.session.engine_state["last_command"], "use:potion")

    def test_an_untrusted_session_gets_no_panel_even_with_a_real_sidebar(self):
        self.session.trusted = False
        self.assertIsNone(panel.panel_context(self.session, {}))
        self.assertEqual(panel.panel_action(self.session, {}, "examine", "sword"), "")
        self.assertEqual(panel.panel_command(self.session, {}, "use", "potion"), "")
