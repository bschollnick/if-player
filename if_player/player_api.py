"""`PlayerAPI` -- the pywebview `js_api` object: a thin, JSON-in/JSON-out
adapter over `game_session.py`'s pure play loop.

Called from JS as `window.pywebview.api.<name>(...)`, returning a
Promise resolving to the JSON-safe dict returned here. This module (and
its `SaveSlotsAPI` mixin) is where `pywebview` is used -- for the
`file://` URI conversion and the native folder/save-file dialogs --
`game_session.py` itself stays pure UI-framework-free, so it can be unit
tested with no window and no filesystem-dialog surface.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import webview
from if_session import session_state
from ink_engine.discovery import mount_game
from ink_engine.engine import UnboundExternalError
from ink_engine.game_folder import (
    GameFolderError,
    plugin_denied_text,
    read_play_layout,
    read_required_plugins,
)
from ink_engine.game_source import GameSourceError, game_identity, open_game_source
from ink_engine.media_resolver import find_cover_image, find_prose_styles
from webview import FileDialog

from if_player import game_session, panel, settings_store
from if_player.game_session import GameSession
from if_player.plugin_sources import discover_session_plugins
from if_player.save_slots_api import SaveSlotsAPI, chosen_path

#: The default layout when a game's own manifest declares none / an
#: unrecognised one.
DEFAULT_PLAY_LAYOUT = "classic"

#: A reference a resolver already built as a URI, rather than a path to
#: turn into one. Two or more scheme characters, so a Windows drive
#: letter ("C:/games/...") stays a path.
_URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]+:")


def _to_file_uri(path_str: str) -> str:
    """Convert one resolved media reference to something an `<img>` can load.

    A game played from a folder resolves to an absolute filesystem path,
    which becomes a `file://` URI. A game played from a bundle has no
    filesystem path at all and resolves to a `data:` URI already carrying
    the bytes; that is returned unchanged, since it is what the page
    loads.

    Args:
        path_str: An absolute path, or a URI a resolver already built.

    Returns:
        A URI an `<img>`/`<video>` `src` can load.
    """
    if _URI_SCHEME.match(path_str):
        return path_str
    return Path(path_str).as_uri()


class PlayerAPI(SaveSlotsAPI):
    """The `js_api` object handed to `webview.create_window()`.

    Inherits `SaveSlotsAPI` (named save slots + quicksave/quickload) --
    a pure file-organization split; every `SaveSlotsAPI` method is a
    real `PlayerAPI`/js_api method, not a separate object JS has to know
    about.

    Attributes:
        session: The one currently-open `GameSession`, or None before
            any game has been opened.
        settings_path: Where the local trust/settings JSON file lives --
            overridable for tests.
    """

    def __init__(self, settings_path: Path | None = None) -> None:
        self.session: GameSession | None = None
        self.settings_path = settings_path or settings_store.default_settings_path()

    def _save_file_path(self, game_dir: Path) -> Path:
        """Return the one save-file path for a game folder.

        Args:
            game_dir: The game folder's real filesystem path.

        Returns:
            A path alongside this instance's own settings file, named
            after the game folder so multiple games never collide.
        """
        return self.settings_path.parent / "saves" / f"{game_identity(game_dir)}.json"

    def _saves_dir(self) -> Path:
        """Return the directory the named game saves live under.

        Returns:
            A directory sibling to `_save_file_path()`'s own auto-save
            file, so a game's every persisted file (auto-save, named
            saves, quicksave) lives under the same settings-relative
            root. The `save_slots` name is what players already have on
            disk; renaming it would orphan their existing saves.
        """
        return self.settings_path.parent / "save_slots"

    # -- Trust ---------------------------------------------------------

    def is_trusted(self, game_dir: str) -> bool:
        """Return whether `game_dir` has already been confirmed trusted.

        Args:
            game_dir: The game folder's own path, as given by the caller.

        Returns:
            True only if this folder was previously trusted via
            `confirm_trust()`.
        """
        settings = settings_store.load_settings(self.settings_path)
        return settings_store.is_folder_trusted(settings, Path(game_dir))

    def confirm_trust(self, game_dir: str) -> dict[str, Any]:
        """Record `game_dir` as trusted, persisting the decision.

        Called only after the JS-side UI has shown a real, explicit
        prompt naming the folder and what trusting it means -- never a
        silent default-yes.

        Args:
            game_dir: The game folder's own path.

        Returns:
            `{"trusted": True}`.
        """
        settings = settings_store.load_settings(self.settings_path)
        settings = settings_store.mark_folder_trusted(settings, Path(game_dir))
        settings_store.save_settings(self.settings_path, settings)
        return {"trusted": True}

    # -- Reader preferences ------------------------------------------------

    def get_reader_prefs(self) -> dict[str, str]:
        """Return the reader's own font-size/text-width preferences.

        Returns:
            `{"font_size", "text_width"}` (see `settings_store.
            get_reader_prefs()`).
        """
        settings = settings_store.load_settings(self.settings_path)
        return settings_store.get_reader_prefs(settings)

    def set_reader_prefs(self, font_size: str, text_width: str) -> dict[str, Any]:
        """Update and persist the reader's own font-size/text-width preferences.

        Args:
            font_size: The submitted font-size value ("small"/"medium"/
                "large" -- an unrecognized value is silently ignored).
            text_width: The submitted text-width value ("narrow"/
                "medium"/"wide" -- same tolerance).

        Returns:
            `{"prefs": {"font_size", "text_width"}}`, the real, saved
            values after applying whichever of the two arguments was
            valid.
        """
        settings = settings_store.load_settings(self.settings_path)
        settings = settings_store.set_reader_prefs(settings, font_size, text_width)
        settings_store.save_settings(self.settings_path, settings)
        return {"prefs": settings_store.get_reader_prefs(settings)}

    def get_strict_externals(self) -> bool:
        """Return whether strict-externals mode is on.

        Returns:
            True if an unbound EXTERNAL raises rather than falling
            through to the story's own Ink stub.
        """
        return settings_store.is_strict_externals(settings_store.load_settings(self.settings_path))

    def set_strict_externals(self, enabled: bool) -> dict[str, Any]:
        """Turn strict-externals mode on or off, persisting the choice.

        Takes effect on the next `open_game()`: a live session keeps the
        mode it was opened with, so a game does not change behaviour
        mid-play.

        Args:
            enabled: Whether an unbound EXTERNAL should raise.

        Returns:
            `{"strict_externals": <bool>}`, the saved value.
        """
        settings = settings_store.load_settings(self.settings_path)
        settings = settings_store.set_strict_externals(settings, enabled)
        settings_store.save_settings(self.settings_path, settings)
        return {"strict_externals": settings_store.is_strict_externals(settings)}

    # -- Turn loop -------------------------------------------------------

    def open_game(self, game_dir: str) -> dict[str, Any]:
        """Open a game folder, resuming its save file if one exists.

        Args:
            game_dir: The game folder's own path.

        Returns:
            A turn context dict (see `_session_context()`) plus
            `"needs_trust"` (True when this folder declares plugins but
            has never been confirmed trusted -- the JS layer should show
            the trust prompt and, once the user agrees, call
            `confirm_trust()` then re-call `open_game()`), `"play_layout"`,
            `"prose_styles"` (the game's own `styles.css` text, or None if
            it supplies none -- the shell injects this after its own
            baseline stylesheet so a game may override or add named
            `<style=name>` prose styles), and `"reader_prefs"`
            (`{"font_size", "text_width"}`, read once here since the
            shell applies these as CSS classes and need not refetch them
            on every turn). `{"error": <message>}` if the folder has no
            real, resolvable compiled story.
        """
        path = Path(game_dir)
        settings = settings_store.load_settings(self.settings_path)
        trusted = settings_store.is_folder_trusted(settings, path)
        reader_prefs = settings_store.get_reader_prefs(settings)
        strict_externals = settings_store.is_strict_externals(settings)

        try:
            source = open_game_source(path)
        except GameSourceError as error:
            return {"error": str(error)}

        required_plugins = read_required_plugins(source)
        if required_plugins and not trusted:
            return {
                "needs_trust": True,
                # The game's own account of what its plugins do and why
                # they need permission -- only it knows that
                # A game's own occupancy plugin missing means no
                # character is anywhere.
                # A game shipping none gets a listing of the names.
                "plugin_denied_text": plugin_denied_text(source),
                "required_plugins": list(required_plugins),
                "play_layout": read_play_layout(source) or DEFAULT_PLAY_LAYOUT,
                "prose_styles": find_prose_styles(source),
                "reader_prefs": reader_prefs,
            }

        self._release_mount()
        mount = mount_game(path) if trusted and source.exists("__init__.py") else None
        plugins = discover_session_plugins(trusted=trusted, mount=mount)
        saved_state = None
        save_path = self._save_file_path(path)
        if save_path.is_file():
            saved_state = json.loads(save_path.read_text(encoding="utf-8"))

        try:
            self.session = game_session.open_game(
                path, saved_state, plugins, required_plugins, trusted, strict_externals=strict_externals, mount=mount, source=source
            )
        except GameFolderError as error:
            return {"error": str(error)}
        except session_state.SaveFormatError as error:
            # Returning here leaves the save file untouched -- _write_save()
            # below would otherwise overwrite the very save being refused.
            return {"error": str(error)}
        except UnboundExternalError as error:
            return {"error": f"Unbound EXTERNAL: {error}"}

        self._write_save()
        context = self._session_context()
        context["needs_trust"] = False
        context["play_layout"] = read_play_layout(source) or DEFAULT_PLAY_LAYOUT
        context["prose_styles"] = find_prose_styles(source)
        context["reader_prefs"] = reader_prefs
        return context

    def choose(self, choice_index: int, turn_count: int) -> dict[str, Any]:
        """Apply one choice and advance the story.

        Args:
            choice_index: The chosen option's index.
            turn_count: The turn count the caller last saw (staleness
                guard).

        Returns:
            `{"stale": True}` on a stale submission, `{"error": ...}` if
            no game is open, the choice is out of range, or strict-externals
            mode caught an unwired EXTERNAL, otherwise a turn context dict.
        """
        if self.session is None:
            return {"error": "No game is open"}
        try:
            result = game_session.choose(self.session, choice_index, turn_count)
        except IndexError:
            return {"error": "Invalid choice"}
        except UnboundExternalError as error:
            return {"error": f"Unbound EXTERNAL: {error}"}
        if result.get("stale"):
            return result
        self._write_save()
        return self._add_media_and_panel(result)

    def undo(self) -> dict[str, Any]:
        """Restore the one-level undo snapshot.

        Returns:
            `{"error": ...}` if no game is open or there is nothing to
            undo, otherwise a turn context dict.
        """
        if self.session is None:
            return {"error": "No game is open"}
        result = game_session.undo(self.session)
        if result is None:
            return {"error": "Nothing to undo"}
        self._write_save()
        return self._add_media_and_panel(result)

    def restart(self) -> dict[str, Any]:
        """Discard the save and start over.

        Returns:
            `{"error": ...}` if no game is open, otherwise a turn context
            dict for the fresh session.
        """
        if self.session is None:
            return {"error": "No game is open"}
        return self._resume_from_state(None)

    # -- Panel -----------------------------------------------------------

    def panel_tab(self, tab_id: str) -> dict[str, Any]:
        """Return the panel's data with `tab_id` selected.

        Args:
            tab_id: Which of the panel's own tabs to show.

        Returns:
            The panel context dict, or `{}` for a game with no panel or
            no open session.
        """
        if self.session is None:
            return {}
        context = panel.panel_context(self.session, self.session.state.globals)
        if context is None:
            return {}
        if any(tab.get("id") == tab_id for tab in context.get("panel_tabs", [])):
            context["panel_active_tab"] = tab_id
            by_tab = context.get("panel_sections_by_tab") or {}
            if tab_id in by_tab:
                context["panel_sections"] = by_tab[tab_id]
        return context

    def panel_action(self, action_id: str, target_id: str) -> dict[str, Any]:
        """Run one of a game panel's read-only row actions.

        Args:
            action_id: Which action the row offered.
            target_id: What the action was invoked on.

        Returns:
            `{"detail_text": <text>}`, or `{"detail_text": ""}` for no
            open session or an unanswered action.
        """
        if self.session is None:
            return {"detail_text": ""}
        text = panel.panel_action(self.session, self.session.state.globals, action_id, target_id)
        return {"detail_text": text}

    def panel_command(self, command_id: str, target_id: str, turn_count: int) -> dict[str, Any]:
        """Run one of a game panel's turn-advancing commands.

        Args:
            command_id: Which command the row offered.
            target_id: What the command was invoked on.
            turn_count: The turn count the caller last saw (staleness
                guard, same shape as `choose()`'s own).

        Returns:
            `{"stale": True}` on a stale submission, `{"panel_detail": ""}`
            for no open session, otherwise the refreshed panel context
            dict plus `"panel_detail"`.
        """
        if self.session is None:
            return {"panel_detail": ""}
        if self.session.state.turn_count != turn_count:
            return {"stale": True}

        text = panel.panel_command(self.session, self.session.state.globals, command_id, target_id)
        self.session.previous_state = self._snapshot_state()
        self._write_save()

        # A command can unlock a choice (giving an item, learning a spell),
        # and the turn's choices were evaluated before it ran. Source
        # redisplays the whole place for exactly this (`items.js:1261`,
        # `dispPlace()` on a command answering "refresh").
        self.session.state.refresh_choices()
        context = self._add_media_and_panel(self._session_context())
        context["panel_detail"] = text
        return context

    def _dialog_start_directory(self) -> str:
        """Return where a file dialog should open.

        pywebview starts a dialog with no `directory` at the process's own
        working directory, which for a launched app is "/". Offering the
        folder the open game came from means reopening lands where the
        player keeps their games.

        Returns:
            The open game's own directory, else the user's Documents
            directory, else their home directory.
        """
        if self.session is not None:
            parent = Path(self.session.game_dir).parent
            if parent.is_dir():
                return str(parent)
        documents = Path.home() / "Documents"
        return str(documents if documents.is_dir() else Path.home())

    # -- Internal ----------------------------------------------------------

    def _release_mount(self) -> None:
        """Release the open game's importable package, if any.

        Called before opening another: a game's plugin modules stay in
        `sys.modules` while it plays, so leaving them there means the
        next game whose package shares this one's name silently gets
        these modules instead of its own.
        """
        if self.session is not None and self.session.mount is not None:
            self.session.mount.unmount()
            self.session.mount = None

    def _write_save(self) -> None:
        """Persist the current session's full state to its save file."""
        if self.session is None:
            return
        save_path = self._save_file_path(self.session.game_dir)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_text(json.dumps(self._snapshot_state()), encoding="utf-8")

    def _session_context(self) -> dict[str, Any]:
        """Build the current session's own turn context.

        Returns:
            The same shape `game_session.choose()`/`undo()` already
            return, via `game_session.turn_context()` directly.

        Raises:
            ValueError: No game is currently open (an internal-call
                contract error, never reachable through the public
                methods above, which all check `self.session` first).
        """
        if self.session is None:
            raise ValueError("_session_context() called with no open session")
        return self._add_media_and_panel(game_session.turn_context(self.session))

    def _add_media_and_panel(self, context: dict[str, Any]) -> dict[str, Any]:
        """Convert `image_urls` to `file://` URIs and attach the panel.

        Args:
            context: A turn context dict, straight from `game_session`.

        Returns:
            The same dict, with the turn's own `image_urls` and each
            choice's converted, and `panel` attached.
        """
        if self.session is not None:
            context["image_urls"] = [_to_file_uri(p) for p in context.get("image_urls", [])]
            for choice in context.get("choices", []):
                choice["image_urls"] = [_to_file_uri(p) for p in choice.get("image_urls", [])]
            context["panel"] = panel.panel_context(self.session, self.session.state.globals)
        return context

    def find_cover(self, game_dir: str) -> str | None:
        """Return a game folder's own cover image, as a `file://` URI.

        Args:
            game_dir: The game folder's own path.

        Returns:
            The `file://` URI, or None if no cover image exists.
        """
        cover = find_cover_image(Path(game_dir))
        return _to_file_uri(cover) if cover is not None else None

    def pick_game_bundle(self) -> str | None:
        """Show a native file-picker filtered to game bundles.

        The primary "open a game" affordance: a bundle is how a game is
        published and what carries the integrity record a player's copy
        is checked against. `pick_game_folder()` is the development
        alternative, for an author editing a game in place.

        Returns:
            The chosen bundle's path, or None if the dialog was
            cancelled.
        """
        result = webview.windows[0].create_file_dialog(
            dialog_type=FileDialog.OPEN,
            directory=self._dialog_start_directory(),
            file_types=("Game bundle (*.zip)", "All files (*.*)"),
        )
        return chosen_path(result)

    def pick_game_folder(self) -> str | None:
        """Show a native folder-picker dialog and return the chosen path.

        **For development**, not the ordinary way to open a game: a game
        folder is what an author edits, while a player receives a bundle
        (`pick_game_bundle()`). A folder carries no integrity record, so
        opening one skips the verification a bundle gets.

        One of two places in `if_player` that touch `pywebview`'s own
        window object directly -- `webview.windows[0]` is this app's own
        single window (`main.py` creates exactly one), matching a
        desktop app's own single-window "open a game" affordance; there
        is no multi-window concept to disambiguate here.

        Returns:
            The chosen folder's path, or None if the dialog was
            cancelled.
        """
        result = webview.windows[0].create_file_dialog(dialog_type=FileDialog.FOLDER, directory=self._dialog_start_directory())
        return chosen_path(result)
