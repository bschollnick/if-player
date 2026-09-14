"""PlayerAPI's own named-save-slot and quicksave/quickload js_api methods
(SaveSlotsAPI). pick_save_destination()/pick_save_file() touch
pywebview's native file dialog directly and are exercised in the shell
smoke tests instead -- not covered here, same reasoning as
PlayerAPI.pick_game_folder()."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from unittest import TestCase

from if_player.player_api import PlayerAPI
from if_player.save_slots_api import chosen_path

FIXTURES = Path(__file__).parent / "fixtures"
SIMPLE_GAME = FIXTURES / "simple_game"


class SaveSlotsAPITestCase(TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.api = PlayerAPI(settings_path=self.tmp / "settings.json")
        self.api.open_game(str(SIMPLE_GAME))


class ListSavesTests(SaveSlotsAPITestCase):
    def test_a_fresh_game_reports_five_empty_slots(self):
        result = self.api.list_saves()
        self.assertEqual(len(result["slots"]), 5)
        self.assertTrue(all(not s["used"] for s in result["slots"]))

    def test_calling_list_saves_before_any_game_is_open_returns_empty(self):
        fresh_api = PlayerAPI(settings_path=self.tmp / "settings2.json")
        self.assertEqual(fresh_api.list_saves(), {"slots": []})


class SaveLoadSlotTests(SaveSlotsAPITestCase):
    def test_saving_to_a_slot_marks_it_used(self):
        self.api.save_to_slot(1, "My save")
        slots = self.api.list_saves()["slots"]
        self.assertTrue(slots[1]["used"])
        self.assertEqual(slots[1]["label"], "My save")

    def test_saving_to_an_out_of_range_slot_returns_an_error(self):
        result = self.api.save_to_slot(99, "")
        self.assertIn("error", result)

    def test_loading_a_slot_resumes_the_saved_turn(self):
        self.api.choose(0, self.api.session.state.turn_count)
        self.api.save_to_slot(0, "After choosing north")

        self.api.restart()
        self.assertNotIn("You went north.", self.api.session.state.last_turn_text)

        context = self.api.load_from_slot(0)
        self.assertIn("You went north.", context["text"])

    def test_loading_an_empty_slot_returns_an_error(self):
        result = self.api.load_from_slot(2)
        self.assertIn("error", result)

    def test_calling_save_to_slot_before_any_game_is_open_returns_an_error(self):
        fresh_api = PlayerAPI(settings_path=self.tmp / "settings2.json")
        self.assertIn("error", fresh_api.save_to_slot(0, ""))

    def test_calling_load_from_slot_before_any_game_is_open_returns_an_error(self):
        fresh_api = PlayerAPI(settings_path=self.tmp / "settings2.json")
        self.assertIn("error", fresh_api.load_from_slot(0))


class DeleteSaveTests(SaveSlotsAPITestCase):
    def test_deleting_a_used_slot_empties_it(self):
        self.api.save_to_slot(1, "To be deleted")
        result = self.api.delete_save(1)
        self.assertEqual(result, {"deleted": True})
        slots = self.api.list_saves()["slots"]
        self.assertFalse(slots[1]["used"])

    def test_deleting_an_already_empty_slot_still_reports_deleted(self):
        result = self.api.delete_save(2)
        self.assertEqual(result, {"deleted": True})

    def test_deleting_an_out_of_range_slot_returns_an_error(self):
        result = self.api.delete_save(99)
        self.assertIn("error", result)

    def test_deleting_one_slot_does_not_affect_others(self):
        self.api.save_to_slot(0, "Slot 0")
        self.api.save_to_slot(1, "Slot 1")
        self.api.delete_save(0)
        slots = self.api.list_saves()["slots"]
        self.assertFalse(slots[0]["used"])
        self.assertTrue(slots[1]["used"])

    def test_calling_delete_save_before_any_game_is_open_returns_an_error(self):
        fresh_api = PlayerAPI(settings_path=self.tmp / "settings2.json")
        self.assertIn("error", fresh_api.delete_save(0))


class ExportImportSaveTests(SaveSlotsAPITestCase):
    def test_exporting_then_importing_round_trips_via_a_real_file(self):
        self.api.choose(0, self.api.session.state.turn_count)
        self.api.save_to_slot(0, "Exported save")

        export_path = self.tmp / "exported.json"
        result = self.api.export_save(0, str(export_path))
        self.assertEqual(result, {"exported": True})
        self.assertTrue(export_path.is_file())

        import_result = self.api.import_save(4, str(export_path))
        self.assertEqual(import_result, {"imported": True})

        context = self.api.load_from_slot(4)
        self.assertIn("You went north.", context["text"])

    def test_exporting_an_empty_slot_returns_an_error(self):
        result = self.api.export_save(2, str(self.tmp / "out.json"))
        self.assertIn("error", result)

    def test_importing_a_missing_file_returns_an_error(self):
        result = self.api.import_save(0, str(self.tmp / "does_not_exist.json"))
        self.assertIn("error", result)

    def test_importing_a_save_from_a_different_game_returns_an_error(self):
        envelope_path = self.tmp / "wrong_game.json"
        envelope_path.write_text('{"quickbbs_if_save_version": 1, "game_name": "not_this_game", "label": "", "state": {}}', encoding="utf-8")
        result = self.api.import_save(0, str(envelope_path))
        self.assertIn("error", result)


class QuicksaveQuickloadTests(SaveSlotsAPITestCase):
    def test_a_fresh_game_has_no_quicksave(self):
        self.assertFalse(self.api.has_quicksave())

    def test_quicksaving_then_has_quicksave_reports_true(self):
        self.api.quicksave()
        self.assertTrue(self.api.has_quicksave())

    def test_quickloading_resumes_the_quicksaved_turn(self):
        self.api.choose(0, self.api.session.state.turn_count)
        self.api.quicksave()

        self.api.restart()
        self.assertNotIn("You went north.", self.api.session.state.last_turn_text)

        context = self.api.quickload()
        self.assertIn("You went north.", context["text"])

    def test_quickloading_with_no_quicksave_returns_an_error(self):
        result = self.api.quickload()
        self.assertIn("error", result)

    def test_quicksaving_never_touches_the_five_named_slots(self):
        self.api.quicksave()
        slots = self.api.list_saves()["slots"]
        self.assertTrue(all(not s["used"] for s in slots))

    def test_a_second_quicksave_overwrites_the_first(self):
        self.api.quicksave()
        self.api.choose(0, self.api.session.state.turn_count)
        self.api.quicksave()

        self.api.restart()
        context = self.api.quickload()
        self.assertIn("You went north.", context["text"])

    def test_calling_quicksave_before_any_game_is_open_returns_an_error(self):
        fresh_api = PlayerAPI(settings_path=self.tmp / "settings2.json")
        self.assertIn("error", fresh_api.quicksave())

    def test_calling_quickload_before_any_game_is_open_returns_an_error(self):
        fresh_api = PlayerAPI(settings_path=self.tmp / "settings2.json")
        self.assertIn("error", fresh_api.quickload())

    def test_has_quicksave_before_any_game_is_open_returns_false(self):
        fresh_api = PlayerAPI(settings_path=self.tmp / "settings2.json")
        self.assertFalse(fresh_api.has_quicksave())


class ChosenPathTests(TestCase):
    """pywebview's dialogs disagree on shape: open/folder answer a tuple of
    paths, save answers a bare string. Indexing [0] blindly turned a save
    path into its first character, "/", which failed only on write."""

    def test_a_save_dialogs_bare_string_is_returned_whole(self):
        self.assertEqual(chosen_path("/Users/me/save-slot-1.json"), "/Users/me/save-slot-1.json")

    def test_an_open_dialogs_tuple_yields_its_first_path(self):
        self.assertEqual(chosen_path(("/Users/me/game.zip",)), "/Users/me/game.zip")

    def test_a_cancelled_dialog_is_none(self):
        for cancelled in (None, (), ""):
            self.assertIsNone(chosen_path(cancelled))


class ExportGuardTests(SaveSlotsAPITestCase):
    def test_exporting_onto_a_directory_reports_an_error(self):
        self.api.save_to_slot(0, "label")
        result = self.api.export_save(0, str(self.tmp))
        self.assertIn("folder", result.get("error", ""))
