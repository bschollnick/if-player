"""`SaveSlotsAPI` -- the named-save-slot and quicksave/quickload
`js_api` methods, split out of `player_api.py` as its own module. A
pure file-organization split: `PlayerAPI` inherits this mixin, so every
method here becomes a real `window.pywebview.api.<name>` method exactly
as if declared directly on `PlayerAPI`.

Reaches into `self.session`/`self.settings_path` and several
underscore-prefixed helpers defined on `PlayerAPI` itself. Not meant to
be used standalone.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import webview
from ink_engine.engine import UnboundExternalError
from ink_engine.game_folder import read_required_plugins
from webview import FileDialog

from if_player.game_session import GameSession, build_saved_state, open_game
from if_player.plugin_sources import discover_session_plugins
from if_player.save_slots import (
    SaveSlotError,
    delete_slot,
    export_slot,
    import_slot,
    list_slots,
    load_slot,
    save_slot,
)
from if_player.settings_store import (
    is_folder_trusted,
    is_strict_externals,
    load_settings,
)

#: The label written for a quicksave -- a quicksave is a single
#: dedicated slot outside the five numbered `save_slots.py` slots.
QUICKSAVE_LABEL = "Quicksave"


def chosen_path(result: object) -> str | None:
    """Return the single path a file dialog chose, whatever shape it used.

    pywebview's dialogs are not symmetric: an open/folder dialog answers a
    TUPLE of paths, while a save dialog answers a bare STRING (macOS:
    `NSSavePanel.filename()`). Indexing [0] blindly turns that string into
    its first character -- "/" -- which then looks like a real destination
    and fails only at the point of writing.

    Args:
        result: Whatever `create_file_dialog()` returned.

    Returns:
        The chosen path, or None when the dialog was cancelled.
    """
    if not result:
        return None
    if isinstance(result, str):
        return result
    return str(result[0])


def _default_export_directory() -> str:
    """Return where an exported save file should be offered.

    pywebview starts a dialog with no `directory` at the process's own
    working directory, which for a launched app is "/" -- the export then
    defaults to writing into the filesystem root. A save the player keeps
    belongs with their own documents.

    Returns:
        The user's Documents directory, or their home directory when no
        Documents directory exists.
    """
    documents = Path.home() / "Documents"
    return str(documents if documents.is_dir() else Path.home())


class SaveSlotsAPI:
    """Named-save-slot and quicksave/quickload methods, mixed into `PlayerAPI`.

    Attributes (all actually owned by `PlayerAPI`, declared here only
    for type-checking this mixin in isolation):
        session: The one currently-open `GameSession`, or None.
        settings_path: The local settings file path.
    """

    session: GameSession | None
    settings_path: Path

    def _write_save(self) -> None:
        """Defined on `PlayerAPI`; declared here for type-checking only."""
        raise NotImplementedError

    def _session_context(self) -> dict[str, Any]:
        """Defined on `PlayerAPI`; declared here for type-checking only."""
        raise NotImplementedError

    def _add_media_and_panel(self, context: dict[str, Any]) -> dict[str, Any]:
        """Defined on `PlayerAPI`; declared here for type-checking only."""
        raise NotImplementedError

    def _saves_dir(self) -> Path:
        """Defined on `PlayerAPI`; declared here for type-checking only."""
        raise NotImplementedError

    def _quicksave_path(self, game_dir: Path) -> Path:
        """Defined on `PlayerAPI`; declared here for type-checking only."""
        raise NotImplementedError

    def _snapshot_state(self) -> dict[str, Any]:
        """Compose the live session's full persistable state dict.

        Shared by every call site that needs the current session as a
        `build_saved_state()`-shaped dict: auto-save, named-slot save,
        quicksave, and the panel-command undo snapshot.

        Returns:
            The current session's state, ready to write to a save file
            or slot.

        Raises:
            ValueError: No game is currently open.
        """
        if self.session is None:
            raise ValueError("_snapshot_state() called with no open session")
        return build_saved_state(self.session.state, self.session.previous_state, self.session.transcript, self.session.engine_state)

    def _resume_from_state(self, saved_state: dict[str, Any] | None) -> dict[str, Any]:
        """Rebuild the live session, re-deriving trust/plugins fresh.

        Shared by `load_from_slot()`/`quickload()`/`PlayerAPI.restart()`
        -- all three replace the live session with a rebuilt one,
        re-deriving plugins/trust fresh exactly like `open_game()` does,
        never reusing the session's own stale `plugins`/`trusted` (a
        slot saved before a folder was trusted, then loaded after, must
        reflect the folder's CURRENT trust state, not the one it had at
        save time -- trust is a property of the folder, never
        serialized into a slot).

        Args:
            saved_state: A previously-saved state dict
                (`build_saved_state()`'s own shape) to resume from, or
                None to start a brand-new game (`restart()`'s own case).

        Returns:
            A turn context dict for the resumed (or fresh) turn, or
            `{"error": ...}` when the rebuilt session cannot run the story
            (see `player_api.py`'s own js_api no-raise contract).

        Raises:
            ValueError: No game is currently open.
        """
        if self.session is None:
            raise ValueError("_resume_from_state() called with no open session")
        game_dir = self.session.game_dir
        settings = load_settings(self.settings_path)
        trusted = is_folder_trusted(settings, game_dir)
        required_plugins = read_required_plugins(game_dir)
        # The open session's mount is reused: it is the same game, and
        # re-mounting would purge modules this session still holds.
        mount = self.session.mount
        plugins = discover_session_plugins(trusted=trusted, mount=mount)
        try:
            self.session = open_game(
                game_dir,
                saved_state,
                plugins,
                required_plugins,
                trusted,
                strict_externals=is_strict_externals(settings),
                mount=mount,
            )
        except UnboundExternalError as error:
            return {"error": f"Unbound EXTERNAL: {error}"}
        self._write_save()
        return self._add_media_and_panel(self._session_context())

    # -- Named save slots --------------------------------------------------

    def list_saves(self) -> dict[str, Any]:
        """List every named save slot for the current game.

        Returns:
            `{"slots": [...]}` (see `save_slots.list_slots()`), or
            `{"slots": []}` if no game is open.
        """
        if self.session is None:
            return {"slots": []}
        return {"slots": list_slots(self._saves_dir(), self.session.game_dir)}

    def save_to_slot(self, slot: int, label: str) -> dict[str, Any]:
        """Snapshot the live session into one named save slot.

        Args:
            slot: The slot index (0-4).
            label: A short, player-chosen label.

        Returns:
            `{"saved": True}`, or `{"error": ...}` if no game is open or
            `slot` is out of range.
        """
        if self.session is None:
            return {"error": "No game is open"}
        try:
            save_slot(self._saves_dir(), self.session.game_dir, slot, self._snapshot_state(), label)
        except SaveSlotError as error:
            return {"error": str(error)}
        return {"saved": True}

    def load_from_slot(self, slot: int) -> dict[str, Any]:
        """Load one named save slot into the live session.

        Loading never mutates the slot file itself, only a subsequent
        explicit `save_to_slot()` call does.

        Args:
            slot: The slot index to load.

        Returns:
            `{"error": ...}` if no game is open, `slot` is out of range,
            or the slot is empty, otherwise a turn context dict for the
            resumed turn.
        """
        if self.session is None:
            return {"error": "No game is open"}
        try:
            saved_state = load_slot(self._saves_dir(), self.session.game_dir, slot)
        except SaveSlotError as error:
            return {"error": str(error)}
        return self._resume_from_state(saved_state)

    def delete_save(self, slot: int) -> dict[str, Any]:
        """Delete one named save slot, if it exists.

        A no-op, not an error, if the slot was already empty.

        Args:
            slot: The slot index to delete.

        Returns:
            `{"deleted": True}`, or `{"error": ...}` if no game is open
            or `slot` is out of range.
        """
        if self.session is None:
            return {"error": "No game is open"}
        try:
            delete_slot(self._saves_dir(), self.session.game_dir, slot)
        except SaveSlotError as error:
            return {"error": str(error)}
        return {"deleted": True}

    def export_save(self, slot: int, destination: str) -> dict[str, Any]:
        """Export one named save slot to a chosen file on disk.

        Writes directly to a path the player chose via
        `pick_save_destination()`.

        Args:
            slot: The slot index to export.
            destination: The file path to write to (as chosen by the
                player, e.g. via a native save-file dialog).

        Returns:
            `{"exported": True}`, or `{"error": ...}` if no game is
            open, `slot` is out of range, or the slot is empty.
        """
        if self.session is None:
            return {"error": "No game is open"}
        try:
            envelope = export_slot(self._saves_dir(), self.session.game_dir, slot)
        except SaveSlotError as error:
            return {"error": str(error)}
        target = Path(destination)
        if target.is_dir():
            return {"error": f"'{destination}' is a folder, not a file"}
        try:
            target.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
        except OSError as error:
            return {"error": f"Could not write '{destination}': {error}"}
        return {"exported": True}

    def import_save(self, slot: int, source: str) -> dict[str, Any]:
        """Import a previously-exported save file into one named slot.

        Args:
            slot: The slot index to write.
            source: The file path to read from (as chosen by the player
                via a native open-file dialog).

        Returns:
            `{"imported": True}`, or `{"error": ...}` if no game is
            open, the file cannot be read/parsed, or it does not match
            this game (see `save_slots.import_slot()`).
        """
        if self.session is None:
            return {"error": "No game is open"}
        try:
            envelope = json.loads(Path(source).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
            return {"error": f"Could not read save file: {error}"}
        try:
            import_slot(self._saves_dir(), self.session.game_dir, slot, envelope)
        except SaveSlotError as error:
            return {"error": str(error)}
        return {"imported": True}

    def pick_save_destination(self, suggested_filename: str) -> str | None:
        """Show a native save-file dialog and return the chosen path.

        Args:
            suggested_filename: The default filename to offer.

        Returns:
            The chosen path, or None if the dialog was cancelled.
        """
        result = webview.windows[0].create_file_dialog(
            dialog_type=FileDialog.SAVE,
            directory=_default_export_directory(),
            save_filename=suggested_filename,
        )
        return chosen_path(result)

    def pick_save_file(self) -> str | None:
        """Show a native open-file dialog for importing a save file.

        Returns:
            The chosen path, or None if the dialog was cancelled.
        """
        result = webview.windows[0].create_file_dialog(
            dialog_type=FileDialog.OPEN,
            directory=_default_export_directory(),
            file_types=("Save files (*.json)", "All files (*.*)"),
        )
        return chosen_path(result)

    # -- Quicksave / quickload --------------------------------------------
    #
    # One dedicated slot, saved/loaded instantly with no label prompt,
    # bound to F5/F9 by the shell's own app.js. Built on the exact same
    # snapshot-copy semantics as the named slots above (quicksaving never
    # touches slots 0-4, quickloading never mutates the quicksave file
    # itself), just addressed by its own reserved path
    # (`_quicksave_path()`) instead of `save_slots.py`'s range-checked
    # slot functions.

    def quicksave(self) -> dict[str, Any]:
        """Instantly snapshot the live session into the one quicksave slot.

        Returns:
            `{"saved": True}`, or `{"error": ...}` if no game is open.
        """
        if self.session is None:
            return {"error": "No game is open"}
        path = self._quicksave_path(self.session.game_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"label": QUICKSAVE_LABEL, "state": self._snapshot_state()}), encoding="utf-8")
        return {"saved": True}

    def quickload(self) -> dict[str, Any]:
        """Instantly restore the live session from the one quicksave slot.

        Returns:
            `{"error": ...}` if no game is open or no quicksave exists,
            otherwise a turn context dict for the resumed turn.
        """
        if self.session is None:
            return {"error": "No game is open"}
        path = self._quicksave_path(self.session.game_dir)
        if not path.is_file():
            return {"error": "No quicksave exists"}
        envelope = json.loads(path.read_text(encoding="utf-8"))
        return self._resume_from_state(envelope["state"])

    def has_quicksave(self) -> bool:
        """Return whether the current game has a quicksave to load.

        Returns:
            False if no game is open or no quicksave file exists yet.
        """
        if self.session is None:
            return False
        return self._quicksave_path(self.session.game_dir).is_file()
