"""Standalone IF Player entry point.

Wires `PlayerAPI` (the `js_api` object) to a single pywebview window
loading the static shell in `if_player/shell/`. This is the ONLY module
that calls `webview.create_window()`/`webview.start()`. Two other modules
reach for `webview` only to open a native file dialog, which needs the
live window reference: `player_api.py`'s `pick_game_folder()` and
`save_slots_api.py`'s import/export dialogs.
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
    # text_select defaults to False, which injects `user-select: none` over
    # the whole body: a reader could not select or copy any of the story.
    webview.create_window(
        "IF Player",
        url=str(SHELL_DIR / "index.html"),
        js_api=api,
        width=1100,
        height=800,
        text_select=True,
    )
    webview.start()


if __name__ == "__main__":
    main()
