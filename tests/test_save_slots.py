"""Named save-slot management -- save_slots.py's own pure functions."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from unittest import TestCase

from if_player.save_slots import (
    MAX_SAVE_SLOTS,
    SaveSlotError,
    export_slot,
    import_slot,
    list_slots,
    load_slot,
    save_slot,
)


class SaveSlotsTestCase(TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.saves_dir = self.tmp / "save_slots"
        self.game_dir = self.tmp / "some_game"
        self.game_dir.mkdir()


class ListSlotsTests(SaveSlotsTestCase):
    def test_an_untouched_game_reports_all_slots_empty(self):
        slots = list_slots(self.saves_dir, self.game_dir)
        self.assertEqual(len(slots), MAX_SAVE_SLOTS)
        self.assertTrue(all(not s["used"] for s in slots))

    def test_a_saved_slot_reports_used_with_its_label_and_turn_count(self):
        save_slot(self.saves_dir, self.game_dir, 2, {"turn_count": 5}, "Before the bridge")
        slots = list_slots(self.saves_dir, self.game_dir)
        self.assertTrue(slots[2]["used"])
        self.assertEqual(slots[2]["label"], "Before the bridge")
        self.assertEqual(slots[2]["turn_count"], 5)

    def test_other_slots_remain_unused_after_one_is_saved(self):
        save_slot(self.saves_dir, self.game_dir, 2, {"turn_count": 5}, "")
        slots = list_slots(self.saves_dir, self.game_dir)
        self.assertFalse(slots[0]["used"])
        self.assertFalse(slots[4]["used"])

    def test_a_corrupted_slot_file_is_reported_as_unused_not_a_crash(self):
        slot_path = self.saves_dir / self.game_dir.resolve().name / "slot0.json"
        slot_path.parent.mkdir(parents=True)
        slot_path.write_text("not valid json {{{", encoding="utf-8")
        slots = list_slots(self.saves_dir, self.game_dir)
        self.assertFalse(slots[0]["used"])


class SaveLoadSlotTests(SaveSlotsTestCase):
    def test_saving_then_loading_round_trips_the_state(self):
        save_slot(self.saves_dir, self.game_dir, 0, {"turn_count": 3, "pointer": "x"}, "My save")
        loaded = load_slot(self.saves_dir, self.game_dir, 0)
        self.assertEqual(loaded, {"turn_count": 3, "pointer": "x"})

    def test_loading_an_empty_slot_raises(self):
        with self.assertRaises(SaveSlotError):
            load_slot(self.saves_dir, self.game_dir, 0)

    def test_saving_out_of_range_slot_raises(self):
        with self.assertRaises(SaveSlotError):
            save_slot(self.saves_dir, self.game_dir, MAX_SAVE_SLOTS, {}, "")
        with self.assertRaises(SaveSlotError):
            save_slot(self.saves_dir, self.game_dir, -1, {}, "")

    def test_loading_never_mutates_the_slot_file(self):
        """Loading is read-only -- only a subsequent explicit save
        overwrites a slot."""
        save_slot(self.saves_dir, self.game_dir, 0, {"turn_count": 1}, "Original")
        load_slot(self.saves_dir, self.game_dir, 0)
        slots = list_slots(self.saves_dir, self.game_dir)
        self.assertEqual(slots[0]["label"], "Original")

    def test_saving_over_an_existing_slot_overwrites_it(self):
        save_slot(self.saves_dir, self.game_dir, 0, {"turn_count": 1}, "First")
        save_slot(self.saves_dir, self.game_dir, 0, {"turn_count": 2}, "Second")
        loaded = load_slot(self.saves_dir, self.game_dir, 0)
        self.assertEqual(loaded["turn_count"], 2)

    def test_a_label_longer_than_100_characters_is_truncated(self):
        save_slot(self.saves_dir, self.game_dir, 0, {}, "x" * 200)
        slots = list_slots(self.saves_dir, self.game_dir)
        self.assertEqual(len(slots[0]["label"]), 100)

    def test_different_games_never_share_a_slot(self):
        other_game = self.tmp / "other_game"
        other_game.mkdir()
        save_slot(self.saves_dir, self.game_dir, 0, {"turn_count": 1}, "Game A")
        with self.assertRaises(SaveSlotError):
            load_slot(self.saves_dir, other_game, 0)


class ExportImportSlotTests(SaveSlotsTestCase):
    def test_exporting_an_empty_slot_raises(self):
        with self.assertRaises(SaveSlotError):
            export_slot(self.saves_dir, self.game_dir, 0)

    def test_exporting_then_importing_round_trips_into_a_different_slot(self):
        save_slot(self.saves_dir, self.game_dir, 0, {"turn_count": 7}, "Exported")
        envelope = export_slot(self.saves_dir, self.game_dir, 0)
        import_slot(self.saves_dir, self.game_dir, 3, envelope)
        loaded = load_slot(self.saves_dir, self.game_dir, 3)
        self.assertEqual(loaded["turn_count"], 7)

    def test_the_export_envelope_carries_the_expected_keys(self):
        save_slot(self.saves_dir, self.game_dir, 0, {"turn_count": 1}, "My label")
        envelope = export_slot(self.saves_dir, self.game_dir, 0)
        self.assertEqual(envelope["quickbbs_if_save_version"], 1)
        self.assertEqual(envelope["game_name"], self.game_dir.resolve().name)
        self.assertEqual(envelope["label"], "My label")

    def test_importing_a_malformed_envelope_raises(self):
        with self.assertRaises(SaveSlotError):
            import_slot(self.saves_dir, self.game_dir, 0, {"not": "an envelope"})

    def test_importing_a_wrong_version_envelope_raises(self):
        envelope = {"quickbbs_if_save_version": 99, "game_name": self.game_dir.resolve().name, "state": {}}
        with self.assertRaises(SaveSlotError):
            import_slot(self.saves_dir, self.game_dir, 0, envelope)

    def test_importing_a_save_from_a_different_game_raises(self):
        other_game = self.tmp / "other_game"
        other_game.mkdir()
        save_slot(self.saves_dir, other_game, 0, {"turn_count": 1}, "")
        envelope = export_slot(self.saves_dir, other_game, 0)
        with self.assertRaises(SaveSlotError):
            import_slot(self.saves_dir, self.game_dir, 0, envelope)
