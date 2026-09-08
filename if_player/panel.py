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

from if_player.game_session import GameSession, bindings_for

logger = logging.getLogger(__name__)


def _sidebar_module(session: GameSession) -> Any:
    """Return a trusted session's own `sidebar` module, if any.

    Args:
        session: The live `GameSession`.

    Returns:
        The module, or None when this session is untrusted or its game
        folder has no `sidebar.py`.
    """
    if not session.trusted:
        return None
    try:
        return importlib.import_module(f"{session.game_dir.name}.sidebar")
    except ModuleNotFoundError:
        return None


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
    module = _sidebar_module(session)
    build = getattr(module, "panel_context", None) if module is not None else None
    if not callable(build):
        return None
    bindings = bindings_for(session.plugins, list(session.engine_state.keys()), session.engine_state, trusted=session.trusted)
    try:
        context = build(session.engine_state, globals_, bindings)
    except Exception:  # pylint: disable=broad-except
        logger.exception("if_player.panel: game %r side panel failed to build; rendering without it", session.game_dir.name)
        return None
    return context if isinstance(context, dict) else None


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
    module = _sidebar_module(session)
    run = getattr(module, "panel_action", None) if module is not None else None
    if not callable(run):
        return ""
    bindings = bindings_for(session.plugins, list(session.engine_state.keys()), session.engine_state, trusted=session.trusted)
    try:
        result = run(session.engine_state, globals_, bindings, action_id, target_id)
    except Exception:  # pylint: disable=broad-except
        logger.exception("if_player.panel: game %r panel_action failed", session.game_dir.name)
        return ""
    return result if isinstance(result, str) else ""


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
    module = _sidebar_module(session)
    run = getattr(module, "panel_command", None) if module is not None else None
    if not callable(run):
        return ""
    bindings = bindings_for(session.plugins, list(session.engine_state.keys()), session.engine_state, trusted=session.trusted)
    try:
        result = run(session.engine_state, globals_, bindings, command_id, target_id)
    except Exception:  # pylint: disable=broad-except
        logger.exception("if_player.panel: game %r panel_command failed", session.game_dir.name)
        return ""
    return result if isinstance(result, str) else ""
