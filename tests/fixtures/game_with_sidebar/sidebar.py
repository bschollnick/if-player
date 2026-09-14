"""A minimal real sidebar module, for exercising panel.py's real dispatch
(argument order, bindings, engine_state mutation) against actual
importable game-folder code rather than a mock."""

from __future__ import annotations

from typing import Any


def panel_context(engine_state: dict[str, Any], globals_: dict[str, Any], bindings: dict[str, Any]) -> dict[str, Any]:
    return {"engine_state": dict(engine_state), "globals": dict(globals_), "binding_names": sorted(bindings.keys())}


def panel_action(engine_state: dict[str, Any], globals_: dict[str, Any], bindings: dict[str, Any], action_id: str, target_id: str) -> str:
    return f"action:{action_id}:{target_id}"


def panel_command(engine_state: dict[str, Any], globals_: dict[str, Any], bindings: dict[str, Any], command_id: str, target_id: str) -> str:
    engine_state["last_command"] = f"{command_id}:{target_id}"
    return f"command:{command_id}:{target_id}"
