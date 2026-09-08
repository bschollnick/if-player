# if-player

A standalone, native desktop player for `.inkj` (compiled Ink) interactive
fiction game folders — no Django, no database, no web browser required.

Built as a thin [pywebview](https://pywebview.flowrl.com/) shell over
[`ink_engine`](../interactive_fiction/), a Django-free Ink interpreter and
plugin engine.

## Design

- `if_player/player_api.py` — `PlayerAPI`, the `js_api` object exposed to
  the webview's own JavaScript, implementing the per-turn play loop
  (open/choose/undo/restart), the panel system
  (panel_tab/panel_action/panel_command), save slots and quicksave/
  quickload, reader preferences, and per-folder trust prompts.
- `if_player/shell/` — the static HTML/CSS/JS UI, calling `PlayerAPI`
  exclusively through `window.pywebview.api.*`.
- Saves and per-folder trust decisions live in the OS per-user data
  directory, never inside a game folder itself (which may be read-only or
  shared).

## Status

In active development.
