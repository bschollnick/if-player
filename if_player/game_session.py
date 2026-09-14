"""The per-turn play loop.

No `pywebview` import anywhere in this module — the per-turn loop is pure
Python state manipulation over `ink_engine.engine.InkRuntimeState`, fully
testable without a real window. `player_api.py` is the thin `js_api`-facing
wrapper around `GameSession` below; this module is where the actual logic
lives, keeping the engine-facing logic separate from the UI-facing adapter.
"""

from __future__ import annotations

import copy
import importlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ink_engine.binding import check_required_plugins, resolve_bindings
from ink_engine.bundle_integrity import verify_bundle
from ink_engine.discovery import MountedGame
from ink_engine.engine import (
    InkRuntimeState,
    load_list_defs,
    load_story_root,
    start_new_story,
)
from ink_engine.game_folder import (
    GameFolderError,
    check_manifest_supported,
    find_main_story_file,
)
from ink_engine.game_source import GameSource, as_source
from ink_engine.media_resolver import FilesystemMediaResolver, parse_media_tags
from ink_engine.plugin import Plugin

#: The module a game ships when its media tags are not plain paths --
#: a converted game whose tags name a character key, say. Absent for a
#: game authored against this engine.
GAME_RESOLVER_MODULE = "image_resolver"

#: Rolling turn-history cap. A plain module constant here; a future
#: config surface may make this adjustable.
MAX_TRANSCRIPT_TURNS = 100


class GameSessionError(Exception):
    """A real, user-facing problem opening or advancing a game session."""


@dataclass
class GameSession:
    """One open game's live state, held in memory (and mirrored to a
    save file).

    Args:
        game_dir: The game folder's real filesystem path.
        state: The live `InkRuntimeState`.
        engine_state: This session's own per-plugin state dict — the
            same dict `ink_engine.binding.resolve_bindings()` allocates
            plugin state slices into, threaded through unchanged.
        transcript: Rolling turn history, oldest first — `{"text",
            "chosen_label"}` entries.
        previous_state: The one prior full state dict (this same shape,
            recursively nestable — restoring it verbatim, itself
            carrying its own `previous_state`, is what makes repeated
            undo keep working), or None before any turn has been taken.
        plugins: Every discoverable `Plugin`, by name — cached at
            `open_game()` time via `discover_plugins()`; NOT rediscovered
            on every turn, since a game's own installed plugin set does
            not change mid-session.
        active_plugin_names: Which plugin names this session activates —
            `open_game()`'s own argument of the same name, kept on the
            session so every LATER rebind (undo, a panel hook) uses the
            same list the opening bind did rather than re-deriving one.

            It must never be re-derived from `engine_state.keys()`, which
            is a different thing: a plugin's `state_key` need not equal
            its `name`, and a game routinely shares a generic plugin's
            slot deliberately. Deriving names from state keys then
            activates the generic plugin in place of the game's own —
            silently, since both answer the same Ink function names.
        trusted: Whether this session's own game folder has been
            confirmed trusted (see `player_api.py`'s trust-prompt flow) —
            gates whether `active_plugin_names` ever resolves to anything
            beyond the empty set.
        strict_externals: Whether an unbound EXTERNAL raises instead of
            falling through to the story's own Ink stub. A development
            aid: it turns "this plugin was never wired" from a story that
            plays wrong into an error at the call site.
        source: Where this game's files are read from — a directory or a
            bundle. `game_dir` remains the game's own location, which is
            what a save is keyed by; this is how its content is reached.
        mount: This game's own importable package, held for the life of
            the session because its plugin modules stay imported while it
            plays. Released when the session is replaced -- otherwise the
            next game whose package shares this one's name gets these
            modules instead of its own.
    """

    game_dir: Path
    state: InkRuntimeState
    engine_state: dict[str, Any] = field(default_factory=dict)
    transcript: list[dict[str, object]] = field(default_factory=list)
    previous_state: dict[str, Any] | None = None
    plugins: dict[str, Plugin] = field(default_factory=dict)
    active_plugin_names: list[str] = field(default_factory=list)
    trusted: bool = False
    strict_externals: bool = False
    mount: MountedGame | None = None
    source: GameSource | None = None


def bindings_for(
    plugins: dict[str, Plugin],
    active_names: list[str],
    engine_state: dict[str, Any],
    *,
    trusted: bool,
    list_defs: dict[str, dict[str, int]] | None = None,
) -> dict[str, Callable[..., Any]]:
    """Return the real bindings a game session should get.

    The ONE place allowed to branch on trust. An untrusted session gets
    no bindings at all, regardless of what the game's own manifest
    declares — every EXTERNAL call then falls through to the story's
    own Ink-side stub.

    Args:
        plugins: Every discoverable `Plugin`, by name (a `GameSession`'s
            own cached `discover_plugins()` result).
        active_names: Which plugin names this game's own manifest
            declares wanting (see `player_api.py`'s own manifest read).
        engine_state: This session's own mutable per-plugin state dict,
            mutated in place by any stateful plugin's `bind()`.
        trusted: Whether this session's game folder has been confirmed
            trusted.
        list_defs: The story's own compiled LIST definitions, handed to
            every plugin `bind()` as its own third argument. Always pass
            it for a real session: a plugin that wants LIST tables and is
            given none binds over an empty one and silently answers wrong.

    Returns:
        The real bindings dict, or `{}` for an untrusted session.
    """
    if not trusted:
        return {}
    resolvable = [name for name in active_names if name in plugins]
    return resolve_bindings(plugins, resolvable, engine_state, list_defs=list_defs)


def session_bindings(session: GameSession) -> dict[str, Callable[..., Any]]:
    """Rebuild one live session's bindings from the names it activated.

    The single way a session's bindings are rebuilt after the opening
    bind -- an undo, a panel hook. Rebinding must always use
    `active_plugin_names`, never `engine_state.keys()` (see
    `GameSession.active_plugin_names`).

    Args:
        session: The live `GameSession`.

    Returns:
        The bindings dict for this session's current state.
    """
    return bindings_for(
        session.plugins,
        session.active_plugin_names,
        session.engine_state,
        trusted=session.trusted,
        list_defs=session.state.list_defs,
    )


def new_game_state(
    root: Any,
    list_defs: dict[str, dict[str, int]],
    engine_bindings: dict[str, Callable[..., Any]],
    initial_globals: dict[str, Any] | None = None,
    *,
    strict_externals: bool = False,
) -> InkRuntimeState:
    """Build a fresh `InkRuntimeState` and run its opening turn.

    Args:
        root: The story's root Container (`load_story_root()`'s result).
        list_defs: The story's LIST definitions.
        engine_bindings: The real bindings this session should get (see
            `bindings_for()`).
        initial_globals: Ink VAR values to set before the opening turn,
            e.g. a character-creation answer.
        strict_externals: Raise rather than fall through to a story's own
            Ink stub when an EXTERNAL has no bound callable.

    Returns:
        A new `InkRuntimeState`, already advanced through its opening
        `continue_story()` call.
    """
    return start_new_story(
        root,
        list_defs,
        engine_bindings=engine_bindings,
        initial_globals=initial_globals,
        strict_externals=strict_externals,
    )


def load_game_state(
    root: Any,
    saved: dict[str, Any],
    list_defs: dict[str, dict[str, int]],
    engine_bindings: dict[str, Callable[..., Any]],
    *,
    strict_externals: bool = False,
) -> InkRuntimeState:
    """Rebuild an `InkRuntimeState` from a saved dict.

    Args:
        root: The story's root Container.
        saved: A previously-saved `to_dict()`-shaped dict.
        list_defs: The story's LIST definitions.
        engine_bindings: The real bindings this session should get,
            re-derived fresh — never itself part of `saved`.
        strict_externals: As for `new_game_state()`; like the bindings, a
            live setting rather than anything `saved` carries.

    Returns:
        The rebuilt `InkRuntimeState`.
    """
    return InkRuntimeState.from_dict(root, saved, list_defs, engine_bindings=engine_bindings, strict_externals=strict_externals)


def build_saved_state(
    state: InkRuntimeState, previous_state: dict[str, Any] | None, transcript: list[dict[str, object]], engine_state: dict[str, Any]
) -> dict[str, Any]:
    """Compose one session's full persistable state dict.

    `InkRuntimeState.to_dict()`'s own fields, plus three bookkeeping
    keys layered on top. This dict IS the save-file format:
    `InkRuntimeState.from_dict()` ignores keys it doesn't recognise, so
    the composition round-trips cleanly.

    Args:
        state: The current `InkRuntimeState`.
        previous_state: The one-level undo target (this same shape,
            recursively nested), or None.
        transcript: The rolling turn history.
        engine_state: This session's own per-plugin state dict.

    Returns:
        The full dict to write to the save file.
    """
    data = state.to_dict()
    data["transcript"] = transcript
    data["previous_state"] = previous_state
    data["engine_state"] = engine_state
    return data


def append_transcript_entry(transcript: list[dict[str, object]], text: str, chosen_label: str | None) -> list[dict[str, object]]:
    """Append one turn to the rolling transcript, capped at
    `MAX_TRANSCRIPT_TURNS`.

    Args:
        transcript: The existing transcript (oldest first).
        text: This turn's own story text.
        chosen_label: The choice text that led to this turn, or None for
            the story's opening turn.

    Returns:
        A new transcript list with the entry appended, trimmed to the
        cap by dropping the OLDEST entries first.
    """
    updated: list[dict[str, object]] = [*transcript, {"text": text, "chosen_label": chosen_label}]
    if len(updated) > MAX_TRANSCRIPT_TURNS:
        updated = updated[-MAX_TRANSCRIPT_TURNS:]
    return updated


def open_game(
    game_dir: Path,
    saved_state: dict[str, Any] | None,
    plugins: dict[str, Plugin],
    active_plugin_names: list[str],
    trusted: bool,
    *,
    initial_globals: dict[str, Any] | None = None,
    strict_externals: bool = False,
    mount: MountedGame | None = None,
    source: GameSource | None = None,
) -> GameSession:
    """Open a game folder, resuming `saved_state` if given, else starting fresh.

    Whether a save exists is the caller's own question to answer
    (`player_api.py` checks its own save-file read) — this function just
    takes the result either way.

    Args:
        game_dir: The game folder's real filesystem path.
        saved_state: A previously-saved state dict (see
            `build_saved_state()`), or None to start a brand-new game.
        plugins: Every discoverable `Plugin` for this game (a
            `discover_plugins()` result the caller built).
        active_plugin_names: Which plugin names this game's own manifest
            declares wanting.
        trusted: Whether this game folder has been confirmed trusted.
        initial_globals: Ink VAR values to set before the opening turn of
            a fresh game, e.g. a character-creation answer. Ignored when
            `saved_state` is given — a resumed game already carries its
            own globals.
        strict_externals: Raise on an unbound EXTERNAL rather than falling
            through to the story's own Ink stub.

    Returns:
        The new `GameSession`, its `state` already advanced through the
        opening turn (for a fresh game) or resumed exactly where the save
        left off (for a saved one).

    Raises:
        GameFolderError: The game declares a manifest version or story
            format this engine cannot play, or names no resolvable
            compiled story.
    """
    source = source if source is not None else as_source(game_dir)
    check_manifest_supported(source)
    _verify_if_bundled(game_dir)
    compiled_json = json.loads(source.read_text(find_main_story_file(source)))
    root = load_story_root(compiled_json)
    list_defs = load_list_defs(compiled_json)

    # The manifest is this game's single declaration of what it needs, so
    # anything in use it does not name means the two have drifted. Checked
    # only for a trusted session, since an untrusted one binds nothing.
    if trusted:
        check_required_plugins(plugins, active_plugin_names, active_plugin_names, root=root)

    if saved_state is None:
        engine_state: dict[str, Any] = {}
        bindings = bindings_for(plugins, active_plugin_names, engine_state, trusted=trusted, list_defs=list_defs)
        state = new_game_state(root, list_defs, bindings, initial_globals=initial_globals, strict_externals=strict_externals)
        return GameSession(
            game_dir=game_dir,
            state=state,
            engine_state=engine_state,
            plugins=plugins,
            active_plugin_names=list(active_plugin_names),
            trusted=trusted,
            strict_externals=strict_externals,
            mount=mount,
            source=source,
        )

    engine_state = copy.deepcopy(saved_state.get("engine_state", {}))
    bindings = bindings_for(plugins, active_plugin_names, engine_state, trusted=trusted, list_defs=list_defs)
    state = load_game_state(root, saved_state, list_defs, bindings, strict_externals=strict_externals)
    return GameSession(
        game_dir=game_dir,
        state=state,
        engine_state=engine_state,
        transcript=list(saved_state.get("transcript", [])),
        previous_state=saved_state.get("previous_state"),
        plugins=plugins,
        active_plugin_names=list(active_plugin_names),
        trusted=trusted,
        strict_externals=strict_externals,
        mount=mount,
        source=source,
    )


def _verify_if_bundled(game_dir: Any) -> None:
    """Check a bundle's recorded hashes before anything is read from it.

    A loose directory has nothing to verify against and is skipped: it is
    the development path, and a game being edited has no build-time
    record to compare with. A bundle is what a player receives, so it is
    checked every time it opens.

    Args:
        game_dir: The game being opened.

    Raises:
        GameFolderError: The bundle has been modified since it was built.
    """
    path = game_dir if isinstance(game_dir, Path) else None
    if path is None or path.suffix.lower() != ".zip":
        return
    problems = verify_bundle(path)
    if problems:
        raise GameFolderError(f"Bundle '{path.name}' has been modified since it was built: {'; '.join(problems)}")


def choose(session: GameSession, choice_index: int, submitted_turn_count: int) -> dict[str, Any]:
    """Apply one choice and advance the story.

    Args:
        session: The live `GameSession` (mutated in place: `state`,
            `engine_state`, `transcript`, `previous_state` all advance).
        choice_index: The chosen option's index into
            `session.state.current_choices`.
        submitted_turn_count: The turn count the caller last saw — a
            defensive staleness guard against a stale or double-submitted
            call.

    Returns:
        `{"stale": True}` if `submitted_turn_count` no longer matches
        `session.state.turn_count` — the caller should not have applied
        this choice. Otherwise `turn_context()`'s own dict: `text`,
        `choices`, `done`, `turn_count`, `image_urls`, `transcript`,
        `can_undo`.

    Raises:
        IndexError: `choice_index` is out of range for
            `session.state.current_choices` — the caller must
            bounds-check first.
    """
    if session.state.turn_count != submitted_turn_count:
        return {"stale": True}

    previous_raw_state = build_saved_state(session.state, session.previous_state, session.transcript, session.engine_state)
    chosen_label = session.state.current_choices[choice_index].text
    session.state.choose(choice_index)
    session.state.continue_story()

    session.transcript = append_transcript_entry(session.transcript, session.state.last_turn_text, chosen_label)
    session.previous_state = previous_raw_state

    return turn_context(session)


def undo(session: GameSession) -> dict[str, Any] | None:
    """Restore the one-level undo snapshot.

    Args:
        session: The live `GameSession`, mutated in place if undo
            succeeds.

    Returns:
        None if there is no `previous_state` to restore -- a real,
        reportable "nothing to undo" case, not a silent no-op. Otherwise
        the restored turn's own context dict, same shape as `choose()`'s
        own success return.
    """
    if session.previous_state is None:
        return None

    restored = session.previous_state
    session.engine_state = copy.deepcopy(restored.get("engine_state", {}))
    bindings = session_bindings(session)
    # The engine's own root Container/list_defs never change across an
    # undo -- the story being played is the same one, only the runtime
    # pointer/eval state resets. Both are always-set InkRuntimeState
    # attributes (engine.py's own __init__), reused directly rather than
    # reloaded from the compiled JSON a second time.
    session.state = InkRuntimeState.from_dict(
        session.state.root,
        restored,
        session.state.list_defs,
        engine_bindings=bindings,
        strict_externals=session.strict_externals,
    )
    session.transcript = list(restored.get("transcript", []))
    session.previous_state = restored.get("previous_state")

    return turn_context(session)


def restart(
    game_dir: Path,
    plugins: dict[str, Plugin],
    active_plugin_names: list[str],
    trusted: bool,
    *,
    initial_globals: dict[str, Any] | None = None,
    strict_externals: bool = False,
    mount: MountedGame | None = None,
    source: GameSource | None = None,
) -> GameSession:
    """Discard any saved state and start over.

    Args:
        game_dir: The game folder's real filesystem path.
        plugins: Every discoverable `Plugin` for this game.
        active_plugin_names: Which plugin names this game's own manifest
            declares wanting.
        trusted: Whether this game folder has been confirmed trusted.
        initial_globals: Ink VAR values to set before the opening turn,
            e.g. a character-creation answer.
        strict_externals: As for `open_game()`.
        mount: As for `open_game()`.
        source: As for `open_game()`.

    Returns:
        A brand-new `GameSession`, identical to `open_game(..., saved_state=None, ...)`.
    """
    return open_game(
        game_dir,
        None,
        plugins,
        active_plugin_names,
        trusted,
        initial_globals=initial_globals,
        strict_externals=strict_externals,
        mount=mount,
        source=source,
    )


def game_resolver_module(session: GameSession) -> Any:
    """Return a trusted session's own media-resolver module, if any.

    Located exactly as `panel.py` locates `sidebar`: through the
    session's own mount, so a bundle's package name -- which its filename
    need not match -- is the one used.

    Args:
        session: The live `GameSession`.

    Returns:
        The module, or None when this session is untrusted or its game
        ships no `image_resolver.py`. A game whose tags are ordinary
        paths needs none.
    """
    if not session.trusted or session.mount is None:
        return None
    try:
        return importlib.import_module(f"{session.mount.package}.{GAME_RESOLVER_MODULE}")
    except ModuleNotFoundError:
        return None


def turn_context(session: GameSession) -> dict[str, Any]:
    """Build one turn's own display context.

    Args:
        session: The live `GameSession`.

    Returns:
        `{"text", "choices", "done", "turn_count", "image_urls",
        "transcript", "can_undo"}` — `image_urls` are resolved absolute
        filesystem path strings; turning these into `file://` URIs is
        `player_api.py`'s own job.
    """
    state = session.state
    resolver = FilesystemMediaResolver(
        session.source if session.source is not None else session.game_dir,
        resolver_module=game_resolver_module(session),
    )
    return {
        "text": state.last_turn_text,
        "choices": [
            {"index": index, "text": choice.text, "image_urls": resolver.resolve(parse_media_tags(choice.tags))}
            for index, choice in enumerate(state.current_choices)
        ],
        "done": state.done and not state.current_choices,
        "turn_count": state.turn_count,
        "image_urls": resolver.resolve(parse_media_tags(state.current_tags)),
        "transcript": session.transcript,
        "can_undo": session.previous_state is not None,
    }
