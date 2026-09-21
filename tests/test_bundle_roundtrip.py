"""A bundle this project builds is a bundle this project can play.

The producer lives in `ink_engine` and the consumer here, with separate
suites, so nothing else checks that what `ink-bundle build` writes is what
`open_game()` can open. That gap is the same cross-repo blind spot that
let the `init_state` contract drift and the `initial_globals` capability
go missing -- both found by reading two repos side by side rather than by
any test.
"""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path
from unittest import TestCase

from ink_engine.bundle_integrity import verify_bundle
from ink_engine.bundler import build_bundle, select_bundle_contents

from if_player.player_api import PlayerAPI

FIXTURES = Path(__file__).parent / "fixtures"
SIMPLE_GAME = FIXTURES / "simple_game"

PLUGIN = "from ink_engine.plugin import Plugin\nPLUGIN = Plugin(name='roundtrip', display_name='Round trip')\n"
SIDEBAR = "def panel_context(**_kwargs):\n    return {'panel_tabs': [{'id': 'x', 'label': 'X'}]}\n"


class BundleRoundTripTests(TestCase):
    """Build a bundle, then open it through the real player path."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

        game = self.tmp / "roundtrip"
        shutil.copytree(SIMPLE_GAME, game)
        (game / "__init__.py").write_text("", encoding="utf-8")
        (game / "plugins.py").write_text(PLUGIN, encoding="utf-8")
        (game / "sidebar.py").write_text(SIDEBAR, encoding="utf-8")
        (game / "manifest.yaml").write_text(
            "MANIFEST_VERSION: 1\nENGINE_FORMAT: ink\nGAME_TITLE: Round Trip\n" "MAIN_STORY_FILE: story.inkj\nREQUIRED_PLUGINS: [roundtrip]\n",
            encoding="utf-8",
        )
        self.game = game

        self.bundle = build_bundle(select_bundle_contents(game), self.tmp / "roundtrip.zip")
        self.api = PlayerAPI(settings_path=self.tmp / "settings.json")

    def _open_bundle(self) -> dict:
        self.api.confirm_trust(str(self.bundle))
        return self.api.open_game(str(self.bundle))

    def test_a_built_bundle_verifies(self):
        self.assertEqual(verify_bundle(self.bundle), [])

    def test_a_built_bundle_opens_and_plays(self):
        context = self._open_bundle()
        self.assertNotIn("error", context)
        self.assertIn("Hello, traveler.", context["text"])

    def test_a_turn_can_be_taken_from_a_bundle(self):
        context = self._open_bundle()
        result = self.api.choose(0, context["turn_count"])
        self.assertNotIn("error", result)
        self.assertGreater(result["turn_count"], context["turn_count"])

    def test_the_games_own_plugin_is_discovered_from_the_bundle(self):
        """The half `zipimport` covers: a bundled game ships real Python."""
        self._open_bundle()
        self.assertIn("roundtrip", self.api.session.plugins)

    def test_the_games_sidebar_loads_from_the_bundle(self):
        self._open_bundle()
        panel = self.api.panel_tab("x")
        self.assertTrue(panel.get("panel_tabs"))

    def test_a_tampered_bundle_is_refused(self):
        """The integrity record is worthless unless a reader checks it."""
        tampered = self.tmp / "tampered.zip"
        with zipfile.ZipFile(self.bundle) as source, zipfile.ZipFile(tampered, "w") as copy:
            copy.comment = source.comment
            for info in source.infolist():
                data = b"EVIL = True\n" if info.filename.endswith("plugins.py") else source.read(info.filename)
                copy.writestr(info.filename, data)
        self.api.confirm_trust(str(tampered))
        self.assertIn("modified", self.api.open_game(str(tampered)).get("error", ""))

    def test_the_companion_readme_is_written_beside_the_bundle(self):
        readme = self.bundle.with_suffix(".md")
        self.assertTrue(readme.is_file())
        self.assertIn("Round Trip", readme.read_text(encoding="utf-8"))

    def test_the_same_game_plays_the_same_from_a_folder(self):
        """Directory and bundle are the same game, not two behaviours."""
        self.api.confirm_trust(str(self.game))
        from_folder = self.api.open_game(str(self.game))
        from_bundle = self._open_bundle()
        self.assertEqual(from_folder["text"], from_bundle["text"])
        self.assertEqual(from_folder["choices"], from_bundle["choices"])

    def test_saves_survive_a_bundle_rename(self):
        """Identity is content-derived, so a download renamed by a browser
        keeps its saves."""
        context = self._open_bundle()
        played = self.api.choose(0, context["turn_count"])
        renamed = self.tmp / "roundtrip (1).zip"
        shutil.copy(self.bundle, renamed)
        self.api.confirm_trust(str(renamed))
        resumed = self.api.open_game(str(renamed))
        self.assertEqual(resumed["turn_count"], played["turn_count"])
        self.assertEqual(resumed["text"], played["text"])
