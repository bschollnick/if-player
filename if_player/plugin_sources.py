"""Plugin-source assembly for `ink_engine.discovery.discover_plugins()`.

Trust-gating and game-folder-importability have no home inside
`ink_engine` itself (it never imports this app's own trust store, never
sees `GameSession.trusted`), so both concerns live here instead:
`ensure_importable()` does a one-time, idempotent `sys.path` insertion
per distinct parent directory so a trusted game folder becomes a real,
importable Python package.
"""

from __future__ import annotations

import sys
from pathlib import Path

import ink_engine.engine_plugins
from ink_engine.discovery import discover_plugins
from ink_engine.plugin import Plugin


def _generic_plugin_sources() -> list[Path]:
    """Return the engine's own shipped plugin directory, always scanned.

    Returns:
        A one-item list holding `ink_engine.engine_plugins`'s own
        directory -- the generic, story-agnostic mechanics that ship with
        the engine regardless of which game is loaded.
    """
    return [Path(ink_engine.engine_plugins.__file__).parent]


def ensure_importable(game_dir: Path) -> str:
    """Make one trusted game folder's real Python package importable, and
    return its dotted module name.

    A game folder is a real Python package on disk (it has its own
    `__init__.py`); it only needs its parent directory on `sys.path` for
    ordinary `importlib.import_module()` to find it, exactly like any
    other installed package.

    Args:
        game_dir: The trusted game folder's real filesystem path (must
            contain `__init__.py`).

    Returns:
        The game's own dotted module name (its bare folder name), ready
        to pass to `discover_plugins()` as a module-mode source.
    """
    parent = str(game_dir.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    return game_dir.name


def discover_session_plugins(game_dir: Path, *, trusted: bool) -> dict[str, Plugin]:
    """Return every `Plugin` this session should even consider.

    Always scans the engine's own generic plugin directory, and only
    additionally scans the game folder itself when `trusted` is True --
    an untrusted game folder is never imported at all, so
    `bindings_for()`'s own trust gate (in `game_session.py`) is
    defence-in-depth, not the only check.

    Args:
        game_dir: The game folder's real filesystem path.
        trusted: Whether this folder has been confirmed trusted (see
            `settings_store.is_folder_trusted()`).

    Returns:
        Every discovered `Plugin`, by name.
    """
    sources: list[Path | str] = list(_generic_plugin_sources())
    if trusted and (game_dir / "__init__.py").is_file():
        sources.append(ensure_importable(game_dir))
    return discover_plugins(sources)
