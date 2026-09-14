"""Plugin-source assembly for `ink_engine.discovery.discover_plugins()`.

Trust-gating has no home inside `ink_engine` itself (it never imports
this app's own trust store, never sees `GameSession.trusted`), so that
decision lives here: this module decides WHICH sources are safe to scan,
and the engine scans exactly what it is given. Making a trusted folder
importable is ordinary packaging, and the engine supplies it.
"""

from __future__ import annotations

from ink_engine.discovery import ENGINE_PLUGIN_PACKAGE, MountedGame, discover_plugins
from ink_engine.plugin import Plugin


def discover_session_plugins(*, trusted: bool, mount: MountedGame | None = None) -> dict[str, Plugin]:
    """Return every `Plugin` this session should even consider.

    Always scans the engine's own generic plugin package, and only
    additionally scans the game folder itself when `trusted` is True --
    an untrusted game folder is never imported at all, so
    `bindings_for()`'s own trust gate (in `game_session.py`) is
    defence-in-depth, not the only check.

    Args:
        trusted: Whether this folder has been confirmed trusted (see
            `settings_store.is_folder_trusted()`).
        mount: The game, made importable by `mount_game()`, or None when
            the caller has not mounted one — in which case only the
            engine's own plugins are found.

    Returns:
        Every discovered `Plugin`, by name.
    """
    sources = [ENGINE_PLUGIN_PACKAGE]
    if trusted and mount is not None:
        sources.append(mount.package)
    return discover_plugins(sources)
