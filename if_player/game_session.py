"""The per-turn play loop.

No `pywebview` import anywhere in this module — the per-turn loop is pure
Python state manipulation over `ink_engine.engine.InkRuntimeState`, fully
testable without a real window. `player_api.py` is the thin `js_api`-facing
wrapper around `GameSession` below; this module is where the actual logic
lives, keeping the engine-facing logic separate from the UI-facing adapter.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ink_engine.binding import resolve_bindings
from ink_engine.engine import InkRuntimeState, load_list_defs, load_story_root
from ink_engine.game_folder import find_main_story_file
from ink_engine.media_resolver import FilesystemMediaResolver, parse_media_tags
from ink_engine.plugin import Plugin

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
        trusted: Whether this session's own game folder has been
            confirmed trusted (see `player_api.py`'s trust-prompt flow) —
            gates whether `active_plugin_names` ever resolves to anything
            beyond the empty set.
    """

    game_dir: Path
    state: InkRuntimeState
    engine_state: dict[str, Any] = field(default_factory=dict)
    transcript: list[dict[str, object]] = field(default_factory=list)
    previous_state: dict[str, Any] | None = None
    plugins: dict[str, Plugin] = field(default_factory=dict)
    trusted: bool = False


def bindings_for(
    plugins: dict[str, Plugin], active_names: list[str], engine_state: dict[str, Any], *, trusted: bool
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

    Returns:
        The real bindings dict, or `{}` for an untrusted session.
    """
    if not trusted:
        return {}
    resolvable = [name for name in active_names if name in plugins]
    return resolve_bindings(plugins, resolvable, engine_state)


def new_game_state(root: Any, list_defs: dict[str, dict[str, int]], engine_bindings: dict[str, Callable[..., Any]]) -> InkRuntimeState:
    """Build a fresh `InkRuntimeState` and run its opening turn.

    Args:
        root: The story's root Container (`load_story_root()`'s result).
        list_defs: The story's LIST definitions.
        engine_bindings: The real bindings this session should get (see
            `bindings_for()`).

    Returns:
        A new `InkRuntimeState`, already advanced through its opening
        `continue_story()` call.
    """
    state = InkRuntimeState(root, list_defs, engine_bindings=engine_bindings)
    state.continue_story()
    return state


def load_game_state(
    root: Any, saved: dict[str, Any], list_defs: dict[str, dict[str, int]], engine_bindings: dict[str, Callable[..., Any]]
) -> InkRuntimeState:
    """Rebuild an `InkRuntimeState` from a saved dict.

    Args:
        root: The story's root Container.
        saved: A previously-saved `to_dict()`-shaped dict.
        list_defs: The story's LIST definitions.
        engine_bindings: The real bindings this session should get,
            re-derived fresh — never itself part of `saved`.

    Returns:
        The rebuilt `InkRuntimeState`.
    """
    return InkRuntimeState.from_dict(root, saved, list_defs, engine_bindings=engine_bindings)


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
    game_dir: Path, saved_state: dict[str, Any] | None, plugins: dict[str, Plugin], active_plugin_names: list[str], trusted: bool
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

    Returns:
        The new `GameSession`, its `state` already advanced through the
        opening turn (for a fresh game) or resumed exactly where the save
        left off (for a saved one).

    Raises:
        GameFolderError: No real, resolvable compiled story file exists
            in `game_dir` (propagated from `find_main_story_file()`).
    """
    main_story_path = find_main_story_file(game_dir)
    compiled_json = json.loads(main_story_path.read_text(encoding="utf-8"))
    root = load_story_root(compiled_json)
    list_defs = load_list_defs(compiled_json)

    if saved_state is None:
        engine_state: dict[str, Any] = {}
        bindings = bindings_for(plugins, active_plugin_names, engine_state, trusted=trusted)
        state = new_game_state(root, list_defs, bindings)
        return GameSession(game_dir=game_dir, state=state, engine_state=engine_state, plugins=plugins, trusted=trusted)

    engine_state = copy.deepcopy(saved_state.get("engine_state", {}))
    bindings = bindings_for(plugins, active_plugin_names, engine_state, trusted=trusted)
    state = load_game_state(root, saved_state, list_defs, bindings)
    return GameSession(
        game_dir=game_dir,
        state=state,
        engine_state=engine_state,
        transcript=list(saved_state.get("transcript", [])),
        previous_state=saved_state.get("previous_state"),
        plugins=plugins,
        trusted=trusted,
    )


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
    bindings = bindings_for(session.plugins, list(session.engine_state.keys()), session.engine_state, trusted=session.trusted)
    # The engine's own root Container/list_defs never change across an
    # undo -- the story being played is the same one, only the runtime
    # pointer/eval state resets. Both are always-set InkRuntimeState
    # attributes (engine.py's own __init__), reused directly rather than
    # reloaded from the compiled JSON a second time.
    session.state = InkRuntimeState.from_dict(session.state.root, restored, session.state.list_defs, engine_bindings=bindings)
    session.transcript = list(restored.get("transcript", []))
    session.previous_state = restored.get("previous_state")

    return turn_context(session)


def restart(game_dir: Path, plugins: dict[str, Plugin], active_plugin_names: list[str], trusted: bool) -> GameSession:
    """Discard any saved state and start over.

    Args:
        game_dir: The game folder's real filesystem path.
        plugins: Every discoverable `Plugin` for this game.
        active_plugin_names: Which plugin names this game's own manifest
            declares wanting.
        trusted: Whether this game folder has been confirmed trusted.

    Returns:
        A brand-new `GameSession`, identical to `open_game(..., saved_state=None, ...)`.
    """
    return open_game(game_dir, None, plugins, active_plugin_names, trusted)


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
    resolver = FilesystemMediaResolver(session.game_dir)
    return {
        "text": state.last_turn_text,
        "choices": list(enumerate(c.text for c in state.current_choices)),
        "done": state.done and not state.current_choices,
        "turn_count": state.turn_count,
        "image_urls": resolver.resolve(parse_media_tags(state.current_tags)),
        "transcript": session.transcript,
        "can_undo": session.previous_state is not None,
    }
