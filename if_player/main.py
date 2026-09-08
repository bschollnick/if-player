"""Standalone IF Player entry point.

Wires `PlayerAPI` (the `js_api` object) to a single pywebview window
loading the static shell in `if_player/shell/`. This is the ONLY module
that calls `webview.create_window()`/`webview.start()` -- `player_api.py`
never imports `webview` itself except inside `pick_game_folder()`'s own
narrow, documented exception (it needs the live window reference for
the native folder dialog).
"""

from __future__ import annotations

from pathlib import Path

import webview

from if_player.player_api import PlayerAPI

SHELL_DIR = Path(__file__).parent / "shell"


def main() -> None:
    """Start the standalone IF Player window."""
    webview.settings["ALLOW_FILE_URLS"] = True
    api = PlayerAPI()
    webview.create_window("IF Player", url=str(SHELL_DIR / "index.html"), js_api=api, width=1100, height=800)
    webview.start()


if __name__ == "__main__":
    main()
