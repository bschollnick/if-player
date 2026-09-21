"""`SaveSlotsAPI` -- the named-save-slot and quicksave/quickload
`js_api` methods, split out of `player_api.py` as its own module. A
pure file-organization split: `PlayerAPI` inherits this mixin, so every
method here becomes a real `window.pywebview.api.<name>` method exactly
as if declared directly on `PlayerAPI`.

The slot logic itself lives in `if_session.game_saves`, shared with the
web application. This module supplies the game identity and the
directory, converts the library's exceptions into the no-raise
`{"error": ...}` contract the UI expects, and owns the file dialogs.

Reaches into `self.session`/`self.settings_path` and several
underscore-prefixed helpers defined on `PlayerAPI` itself. Not meant to
be used standalone.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import webview
from if_session import session_state

# Aliased because the js_api methods below share these three names.
# Python would resolve the bare names correctly -- a method body reads
# module globals, not the class namespace -- but it reads as recursion.
from if_session.game_saves import (
    GameSaveError,
    GameSavesDirectory,
    delete_game_save,
    export_game_save,
    has_quicksave as library_has_quicksave,
    import_game_save,
    is_from_another_build,
    list_game_saves,
    load_game_save,
    quickload as library_quickload,
    quicksave as library_quicksave,
    save_game,
)
from ink_engine.engine import UnboundExternalError
from ink_engine.game_folder import read_required_plugins
from ink_engine.game_source import game_build, game_identity
from webview import FileDialog

from if_player.game_session import GameSession, build_saved_state, open_game
from if_player.plugin_sources import discover_session_plugins
from if_player.settings_store import (
    is_folder_trusted,
    is_strict_externals,
    load_settings,
)

#: How many numbered save slots this player offers per game.
MAX_SAVE_SLOTS = 5


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
    if isinstance(result, (tuple, list)):
        return str(result[0])
    # Neither shape pywebview documents. Treating it as a path would
    # produce a plausible-looking destination that fails at write time.
    return None


def _now() -> str:
    """Return the current time as the ISO-8601 string a save records.

    The library never reads a clock, so the application supplies one.
    """
    return datetime.now(UTC).isoformat()


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

    def _game_saves_directory(self) -> GameSavesDirectory:
        """Return the directory this game's saves are kept in."""
        return GameSavesDirectory(self._saves_dir())

    def _game_build(self) -> str:
        """Return which build of the open game this is.

        Compared against the build a save recorded, so a player is
        cautioned when a save predates a rebuild rather than losing it.
        """
        if self.session is None:
            return ""
        return game_build(self.session.game_dir)

    def _game_id(self) -> str:
        """Return the open game's identity, as the library's partition key.

        Raises:
            ValueError: No game is currently open.
        """
        if self.session is None:
            raise ValueError("_game_id() called with no open session")
        return game_identity(self.session.game_dir)

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
        except session_state.SaveFormatError as error:
            # Returning here leaves the live session and the save file as
            # they were -- _write_save() below would otherwise overwrite
            # the save with a session that never loaded.
            return {"error": str(error)}
        except UnboundExternalError as error:
            return {"error": f"Unbound EXTERNAL: {error}"}
        self._write_save()
        return self._add_media_and_panel(self._session_context())

    # -- Named save slots --------------------------------------------------

    def list_saves(self) -> dict[str, Any]:
        """List every named save slot for the current game.

        Returns:
            `{"slots": [...]}` (see `game_saves.list_game_saves()`), or
            `{"slots": []}` if no game is open.
        """
        if self.session is None:
            return {"slots": []}
        current_build = self._game_build()
        slots = list_game_saves(
            self._game_id(),
            saves_in=self._game_saves_directory(),
            maximum_gamesave_slots=MAX_SAVE_SLOTS,
        )
        for entry in slots:
            entry["other_build"] = entry["used"] and is_from_another_build(entry, current_build)
        return {"slots": slots}

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
            save_game(
                self._game_id(),
                slot,
                self._snapshot_state(),
                label,
                saves_in=self._game_saves_directory(),
                maximum_gamesave_slots=MAX_SAVE_SLOTS,
                saved_at=_now(),
                game_build=self._game_build(),
            )
        except GameSaveError as error:
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
            saved_state = load_game_save(
                self._game_id(),
                slot,
                saves_in=self._game_saves_directory(),
                maximum_gamesave_slots=MAX_SAVE_SLOTS,
            )
        except (GameSaveError, session_state.SaveFormatError) as error:
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
            delete_game_save(
                self._game_id(),
                slot,
                saves_in=self._game_saves_directory(),
                maximum_gamesave_slots=MAX_SAVE_SLOTS,
            )
        except GameSaveError as error:
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
            envelope = export_game_save(
                self._game_id(),
                slot,
                saves_in=self._game_saves_directory(),
                maximum_gamesave_slots=MAX_SAVE_SLOTS,
            )
        except GameSaveError as error:
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
            this game (see `game_saves.import_game_save()`).
        """
        if self.session is None:
            return {"error": "No game is open"}
        try:
            envelope = json.loads(Path(source).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
            return {"error": f"Could not read save file: {error}"}
        try:
            written = import_game_save(
                self._game_id(),
                slot,
                envelope,
                saves_in=self._game_saves_directory(),
                maximum_gamesave_slots=MAX_SAVE_SLOTS,
                saved_at=_now(),
            )
        except GameSaveError as error:
            return {"error": str(error)}
        # Importing only copies the file in; whether it plays is settled
        # at load. The shell cautions there too, off the slot listing.
        return {"imported": True, "other_build": is_from_another_build(written, self._game_build())}

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
    # touches slots 0-4, quickloading never mutates the quicksave itself),
    # addressed by the library's own reserved slot number.

    def quicksave(self) -> dict[str, Any]:
        """Instantly snapshot the live session into the one quicksave slot.

        Returns:
            `{"saved": True}`, or `{"error": ...}` if no game is open.
        """
        if self.session is None:
            return {"error": "No game is open"}
        try:
            library_quicksave(
                self._game_id(),
                self._snapshot_state(),
                saves_in=self._game_saves_directory(),
                saved_at=_now(),
                game_build=self._game_build(),
            )
        except GameSaveError as error:
            return {"error": str(error)}
        return {"saved": True}

    def quickload(self) -> dict[str, Any]:
        """Instantly restore the live session from the one quicksave slot.

        Returns:
            `{"error": ...}` if no game is open, no quicksave exists, or
            the quicksave cannot be read, otherwise a turn context dict
            for the resumed turn.
        """
        if self.session is None:
            return {"error": "No game is open"}
        try:
            saved_state = library_quickload(self._game_id(), saves_in=self._game_saves_directory())
        except (GameSaveError, session_state.SaveFormatError) as error:
            return {"error": str(error)}
        return self._resume_from_state(saved_state)

    def has_quicksave(self) -> bool:
        """Return whether the current game has a quicksave to load.

        Returns:
            False if no game is open or no quicksave exists yet.
        """
        if self.session is None:
            return False
        return library_has_quicksave(self._game_id(), saves_in=self._game_saves_directory())
