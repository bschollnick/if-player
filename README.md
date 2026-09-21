# if-player

**Date Created:** 2026-09-08
**Last Updated:** 2026-09-19
**Last Reviewed:** 2026-09-19

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
- `if_player/save_slots_api.py` — `SaveSlotsAPI`, the named-slot and
  quicksave/quickload half of that API. `PlayerAPI` inherits it, so every
  method here is a real `window.pywebview.api.*` method; the split is
  file organization, not a second interface.
- `if_player/game_session.py` — the per-turn play loop, as pure Python
  state over `ink_engine.engine.InkRuntimeState`. No `pywebview` import,
  so it is testable without a window.
- `if_player/panel.py` — side-panel dispatch. A trusted game folder may
  ship a `sidebar.py` exposing `panel_context()`/`panel_action()`/
  `panel_command()`; this locates and calls it.
- `if_player/plugin_sources.py` — assembles the plugin sources handed to
  `ink_engine.discovery.discover_plugins()`. Trust-gating lives here
  rather than in `ink_engine`, which never sees this app's trust store.
- `if_player/settings_store.py` — the local JSON store holding per-folder
  trust decisions and reader preferences.
- `if_player/main.py` — the entry point, and the only module that calls
  `webview.create_window()`/`webview.start()`.
- `if_player/shell/` — the static HTML/CSS/JS UI, calling `PlayerAPI`
  exclusively through `window.pywebview.api.*`.
- Saves and per-folder trust decisions live in the platform's per-user
  config directory — `$XDG_CONFIG_HOME/if_player/` where that is set,
  otherwise `~/.config/if_player/` — never inside a game folder itself,
  which may be read-only or shared.

## Design document

[`docs/if_player_design.md`](docs/if_player_design.md) — guiding principles,
architecture, per-module reference, and the trust model.

## Status

In active development.
