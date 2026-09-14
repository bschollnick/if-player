"""Game side-panel dispatch.

A trusted game folder may ship a `sidebar.py` exposing `panel_context()`/
`panel_action()`/`panel_command()`; this module locates and calls it,
with the same real bindings `game_session.bindings_for()` builds for a
story turn. An untrusted session, or a game with no `sidebar.py`, gets
no panel at all -- `None` means absent, never an error.
"""

from __future__ import annotations

import importlib
import logging
from typing import Any

from ink_engine import game_panel

from if_player.game_session import GameSession, session_bindings

logger = logging.getLogger(__name__)


def _sidebar_module(session: GameSession) -> Any:
    """Return a trusted session's own `sidebar` module, if any.

    Args:
        session: The live `GameSession`.

    Returns:
        The module, or None when this session is untrusted or its game
        has no `sidebar.py`. The package comes from the session's own
        mount: a bundle's package name is whatever it holds inside,
        which its filename need not match.
    """
    if not session.trusted:
        return None
    try:
        package = session.mount.package if session.mount is not None else session.game_dir.name
        return importlib.import_module(f"{package}.sidebar")
    except ModuleNotFoundError:
        return None


def _hook_arguments(session: GameSession, globals_: dict[str, Any]) -> dict[str, Any]:
    """Build the three arguments every panel hook receives.

    Args:
        session: The live `GameSession`.
        globals_: The runtime's own Ink globals.

    Returns:
        The `module`/`engine_state`/`globals_`/`bindings` keyword bundle
        `ink_engine.game_panel`'s own hook callers expect.
    """
    return {
        "engine_state": session.engine_state,
        "globals_": globals_,
        "bindings": session_bindings(session),
        "logger": logger,
    }


def panel_context(session: GameSession, globals_: dict[str, Any]) -> dict[str, Any] | None:
    """Return the side-panel data a game supplies for its play page.

    Args:
        session: The live `GameSession`.
        globals_: The runtime's own Ink globals.

    Returns:
        The panel's context dict, or None when this game supplies no
        panel (or is untrusted, or its module exposes no
        `panel_context()`, or it raises).
    """
    return game_panel.panel_context(_sidebar_module(session), **_hook_arguments(session, globals_))


def panel_action(session: GameSession, globals_: dict[str, Any], action_id: str, target_id: str) -> str:
    """Run one of a game panel's read-only row actions.

    Args:
        session: The live `GameSession`.
        globals_: The runtime's own Ink globals.
        action_id: Which action the row offered.
        target_id: What the action was invoked on.

    Returns:
        Whatever text the game's own `panel_action()` answers, or `""`
        for an untrusted session, a game with no panel, or one that
        raises.
    """
    return game_panel.panel_action(_sidebar_module(session), **_hook_arguments(session, globals_), action_id=action_id, target_id=target_id)


def panel_command(session: GameSession, globals_: dict[str, Any], command_id: str, target_id: str) -> str:
    """Run one of a game panel's turn-advancing commands (Use/Cast/Give/Drop).

    Mutates `session.engine_state` in place, exactly like a stateful
    binding invoked mid-turn does. Does NOT call
    `InkRuntimeState.continue_story()` or advance Ink's own turn count --
    a panel command is not a story choice.

    Args:
        session: The live `GameSession` (its `engine_state` mutated in
            place by a successful command).
        globals_: The runtime's own Ink globals.
        command_id: Which command the row offered.
        target_id: What the command was invoked on.

    Returns:
        Whatever text the game's own `panel_command()` answers, or `""`
        for an untrusted session, a game with no panel, or one that
        raises.
    """
    return game_panel.panel_command(_sidebar_module(session), **_hook_arguments(session, globals_), command_id=command_id, target_id=target_id)
