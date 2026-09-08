"""Named save-slot management.

Five numbered, labeled slots per game folder (`MAX_SAVE_SLOTS`), each a
plain JSON file on disk. `save_slot()`/`load_slot()` are snapshot copies,
never a live link: saving copies the live session's state into the slot
file, loading copies the slot file's own state back into the live
session; playing on afterward never mutates a slot file until the next
explicit save. `export_slot()`/`import_slot()` use a versioned envelope
shape (`quickbbs_if_save_version`, `game_name`, `state`) so a save file
can be moved between installations and validated before being trusted.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: How many save slots each game folder gets.
MAX_SAVE_SLOTS = 5

#: The envelope version `export_slot()` writes and `import_slot()`
#: requires.
SAVE_ENVELOPE_VERSION = 1


class SaveSlotError(Exception):
    """A real, user-facing problem with a save-slot operation."""


def _slot_path(saves_dir: Path, game_dir: Path, slot: int) -> Path:
    """Return one save slot's own file path.

    Args:
        saves_dir: The app's own saves directory (sibling to the
            settings file -- see `PlayerAPI._save_file_path()`).
        game_dir: The game folder's real filesystem path.
        slot: The slot index (`0` to `MAX_SAVE_SLOTS - 1`).

    Returns:
        `<saves_dir>/<game_dir.name>/slot<N>.json`.

    Raises:
        SaveSlotError: `slot` is outside the valid range.
    """
    if not 0 <= slot < MAX_SAVE_SLOTS:
        raise SaveSlotError(f"Slot {slot} is out of range (0-{MAX_SAVE_SLOTS - 1})")
    return saves_dir / game_dir.resolve().name / f"slot{slot}.json"


def list_slots(saves_dir: Path, game_dir: Path) -> list[dict[str, Any]]:
    """Return every slot's own summary for one game folder.

    Args:
        saves_dir: The app's own saves directory.
        game_dir: The game folder's real filesystem path.

    Returns:
        One entry per slot `0..MAX_SAVE_SLOTS-1`, in order:
        `{"slot", "used", "label", "turn_count"}`. `used` is False for
        an empty slot, whose `label`/`turn_count` are then both None.
    """
    slots: list[dict[str, Any]] = []
    for slot in range(MAX_SAVE_SLOTS):
        path = _slot_path(saves_dir, game_dir, slot)
        if not path.is_file():
            slots.append({"slot": slot, "used": False, "label": None, "turn_count": None})
            continue
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            slots.append({"slot": slot, "used": False, "label": None, "turn_count": None})
            continue
        slots.append(
            {
                "slot": slot,
                "used": True,
                "label": envelope.get("label", ""),
                "turn_count": envelope.get("state", {}).get("turn_count"),
            }
        )
    return slots


def save_slot(saves_dir: Path, game_dir: Path, slot: int, state: dict[str, Any], label: str) -> None:
    """Snapshot the live session's state into one save slot.

    A copy, not a live link -- subsequent play never mutates this slot
    until the next explicit `save_slot()` call.

    Args:
        saves_dir: The app's own saves directory.
        game_dir: The game folder's real filesystem path.
        slot: The slot index to write.
        state: The full session state dict (`build_saved_state()`'s own
            shape).
        label: A short, player-chosen label (e.g. "Before the bridge"),
            truncated to 100 characters.

    Raises:
        SaveSlotError: `slot` is outside the valid range.
    """
    path = _slot_path(saves_dir, game_dir, slot)
    path.parent.mkdir(parents=True, exist_ok=True)
    envelope = {"label": label[:100], "state": state}
    path.write_text(json.dumps(envelope), encoding="utf-8")


def load_slot(saves_dir: Path, game_dir: Path, slot: int) -> dict[str, Any]:
    """Return one save slot's own state dict, unmodified.

    Loading itself never mutates the slot file -- only a subsequent
    explicit `save_slot()` call overwrites it.

    Args:
        saves_dir: The app's own saves directory.
        game_dir: The game folder's real filesystem path.
        slot: The slot index to read.

    Returns:
        The saved `state` dict (ready to pass to `game_session.open_game()`
        as `saved_state`).

    Raises:
        SaveSlotError: `slot` is outside the valid range, or empty.
    """
    path = _slot_path(saves_dir, game_dir, slot)
    if not path.is_file():
        raise SaveSlotError(f"Slot {slot} is empty")
    envelope = json.loads(path.read_text(encoding="utf-8"))
    return envelope["state"]  # type: ignore[no-any-return]


def export_slot(saves_dir: Path, game_dir: Path, slot: int) -> dict[str, Any]:
    """Return one save slot as a portable envelope, ready to write to a file.

    Args:
        saves_dir: The app's own saves directory.
        game_dir: The game folder's real filesystem path.
        slot: The slot index to export.

    Returns:
        `{"quickbbs_if_save_version", "game_name", "label", "state"}` --
        `game_name` is the game folder's own bare directory name, since
        a standalone game folder has no other stable identity to key on.

    Raises:
        SaveSlotError: `slot` is outside the valid range, or empty.
    """
    path = _slot_path(saves_dir, game_dir, slot)
    if not path.is_file():
        raise SaveSlotError(f"Slot {slot} is empty")
    envelope = json.loads(path.read_text(encoding="utf-8"))
    return {
        "quickbbs_if_save_version": SAVE_ENVELOPE_VERSION,
        "game_name": game_dir.resolve().name,
        "label": envelope.get("label", ""),
        "state": envelope["state"],
    }


def import_slot(saves_dir: Path, game_dir: Path, slot: int, envelope: dict[str, Any]) -> None:
    """Validate and write an imported envelope into one save slot.

    Args:
        saves_dir: The app's own saves directory.
        game_dir: The game folder's real filesystem path.
        slot: The slot index to write.
        envelope: A decoded JSON envelope, expected to match
            `export_slot()`'s own shape.

    Raises:
        SaveSlotError: `slot` is out of range, or `envelope` is
            malformed (not the right shape/version, or names a
            different game).
    """
    if not isinstance(envelope, dict) or envelope.get("quickbbs_if_save_version") != SAVE_ENVELOPE_VERSION:
        raise SaveSlotError("Not a recognized save file")
    if envelope.get("game_name") != game_dir.resolve().name:
        raise SaveSlotError("This save file is from a different game")
    if not isinstance(envelope.get("state"), dict):
        raise SaveSlotError("Not a recognized save file")
    save_slot(saves_dir, game_dir, slot, envelope["state"], str(envelope.get("label", "")))


def delete_slot(saves_dir: Path, game_dir: Path, slot: int) -> None:
    """Delete one save slot, if it exists.

    Args:
        saves_dir: The app's own saves directory.
        game_dir: The game folder's real filesystem path.
        slot: The slot index to delete.

    Raises:
        SaveSlotError: `slot` is outside the valid range.
    """
    path = _slot_path(saves_dir, game_dir, slot)
    path.unlink(missing_ok=True)
