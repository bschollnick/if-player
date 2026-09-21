# if_player — Design Document

**Document version:** 1.3  
**Author:** Benjamin Schollnick  

**Date Created:** 2026-09-19  
**Last Updated:** 2026-09-21  
**Last Reviewed:** 2026-09-21

A standalone, native desktop player for compiled-Ink (`.inkj`) game
folders. Written against the shipped code: `if_player` 0.8.0 on
`ink-engine` 1.0.0, with 178 tests passing.

Its exception handling is documented separately, in
[`if_player_exceptions.md`](if_player_exceptions.md).

---

## 1. Guiding Principles

### 1.1 This application uses the engine; it does not own it

`ink_engine` is the interactive fiction in this Interactive Fiction
Player: it interprets the story and owns the plugin contract. This player
is largely a desktop window around it, with `if_session` supplying the
save envelope and transcript around that. The engine knows nothing about
this application — the dependency runs one way, and nothing in
`ink_engine` imports this repo.

Every capability that both this player and another application using the
engine need belongs in that library; anything only a desktop player needs
belongs here.

The practical test: a feature added here that another application using
the engine would also want is in the wrong place.

Trust-gating does not follow that test, and is not meant to. Deciding
what may be imported is an application-level choice, not an engine one.
`ink_engine` has to assume everything it is handed is trusted — the
alternative is an approval system around every script and every file it
touches, which is not a library's job. So the engine loads exactly the
sources it is given, and this application decides what those are.

### 1.2 A game folder is untrusted until the player says otherwise

A game folder may ship its own Python: plugins, a `sidebar.py`. Importing
that is running someone else's code, so it does not happen until the person
at the keyboard confirms this specific folder.

The gate is applied where the import happens, not only where the code runs:
`discover_session_plugins()` scans the engine's own plugin package always,
and the game folder **only** when `trusted` is True, so an untrusted folder
is never imported at all. `bindings_for()` checks trust again before wiring
anything, which is defence in depth rather than the only check.

The unit of trust is the folder path. A folder confirmed once stays
confirmed, and its contents are not hashed or re-checked, so a game
updated in place runs its new code without asking again. Content-based
trust was not weighed against path-based trust; the path is simply what
the settings file records.

### 1.3 Nothing is written inside a game folder

A game folder may be read-only, on shared storage, or inside a bundle. Saves,
trust decisions and reader preferences therefore live in the platform's
per-user config directory — `$XDG_CONFIG_HOME/if_player/` where that is set,
otherwise `~/.config/if_player/`. A game folder is input only.

### 1.4 How deep undo goes is this application's decision

`if_session` stores whatever undo history it is handed and caps nothing:
each saved state carries the state before it, so the structure nests one
level per turn and a save file grows with the length of the playthrough.
That is the library behaving correctly — it cannot know how far back a
given application should let a player reach.

Pruning is therefore this application's call, and **`if_player` has not
yet made it**. Undo currently reaches back to the first turn, at a cost
that rises with playthrough length: measured on a two-line looping story,
a save file is 11 KB at 10 turns, 360 KB at 100, and 1 MB at 200.
Nothing has forced a limit yet. When one is chosen it belongs here, beside the
transcript's own `MAX_TRANSCRIPT_TURNS` cap, not in the library.

### 1.5 Only one module knows there is a window

The per-turn loop is pure Python state manipulation over
`ink_engine.engine.InkRuntimeState`. `main.py` is the only module that calls
`webview.create_window()`/`webview.start()`; two others reach for `webview`
solely to raise a native file dialog, which needs the live window reference.

That separation is what makes the play loop testable without a window, and
it is why `game_session.py` carries the logic while `player_api.py` stays a
thin surface over it.

### 1.6 As playable and customisable as is reasonable, with a ceiling

The goal is to play any Ink game well, and to let a game and a reader
adjust what they reasonably can: a game supplies its own prose styles,
side panel, media resolver and layout choice; a reader sets font size and
text width.

It will not match a game with its own custom-built interface, and cannot.
A bespoke interface is designed for one game and one vision. This player
is one shell serving every game, including games it has never seen.

**Where customisation stops has not been decided.** The hooks that exist
were added when a game needed them, not derived from a boundary drawn in
advance. A reader looking here for a rule about what will and will not be
added should not infer one.

---

## 2. Purpose

**What it is.** A desktop application that opens a game folder or bundle and
plays it: one window, no browser, no database, no server.

**What it owns.** The window and its static UI; the per-turn play loop over
the engine's runtime state; save slots, quicksave and transcripts; reader
preferences; per-folder trust decisions; and the side panel a game may
supply.

**What it does not own.** The Ink runtime, the plugin contract and the
game manifest format belong to `ink_engine`; the save envelope, the
transcript and one turn's context belong to `if_session`.

---

## 3. High-Level Architecture

```
  webview (WKWebView / WebView2 / WebKitGTK)
        │  window.pywebview.api.*
        ▼
  ┌───────────────────────────────────────────────┐
  │ player_api.py    PlayerAPI                    │  the js_api surface
  │   + save_slots_api.py  SaveSlotsAPI (mixin)   │
  └───────────────────────────────────────────────┘
        │                    │                │
        ▼                    ▼                ▼
  game_session.py      settings_store.py   panel.py
  the play loop        trust + prefs       game side panel
        │                                      │
        ▼                                      ▼
  plugin_sources.py  ──────────────────►  a game's sidebar.py
  trust-gated discovery                   (trusted folders only)
        │
        ▼
  ink_engine  (runtime, plugins)  +  if_session  (saves, transcript)
```

Every arrow runs downward. The UI never reaches past `PlayerAPI`, and no
module below it knows a window exists.

---

## 4. Component Reference

### 4.1 `player_api.py`

**What does this do?** Gives the on-screen interface the only set of
actions it is allowed to take — open a game, pick a choice, undo, change
the text size — so a button in the window can never reach further into
the application than the list here.

**What is its purpose?** `PlayerAPI`, the object handed to
`webview.create_window(js_api=...)`: every public method becomes one
`window.pywebview.api.<name>` call. It holds one optional `GameSession`
and the settings path, and delegates the work to the modules below.

| Group | Methods |
|---|---|
| Trust | `is_trusted`, `confirm_trust` |
| Preferences | `get_reader_prefs`, `set_reader_prefs`, `get_strict_externals`, `set_strict_externals` |
| Play loop | `open_game`, `start_new_game`, `choose`, `undo`, `restart` |
| Side panel | `panel_tab`, `panel_action`, `panel_command` |
| Files | `find_cover`, `pick_game_bundle`, `pick_game_folder` |

### 4.2 `save_slots_api.py`

**What does this do?** Lets a player keep several games in progress at
once, and hand one to somebody else as a file.

**What is its purpose?** `SaveSlotsAPI`, a mixin `PlayerAPI` inherits, so
its methods are part of the same `window.pywebview.api.*` surface; the
split is file organization, not a second interface. Covers `list_saves`,
`save_to_slot`, `load_from_slot`, `delete_save`, `export_save`,
`import_save`, `pick_save_destination`, `pick_save_file`, `quicksave`,
`quickload`, `has_quicksave`.

Named slots and quicksave both resolve under `settings_path.parent /
"save_slots"`, so every persisted file for a game sits under one
settings-relative root.

### 4.3 `game_session.py`

**What does this do?** Moves the story forward one turn at a time, and
remembers where the player has been.

**What is its purpose?** The play loop, with no `pywebview` import at
all, which is what makes it testable without a window. Holds
`GameSession` and the functions that move a story forward: `open_game`,
`choose`, `undo`, `restart`, plus state construction
(`new_game_state`, `load_game_state`, `build_saved_state`), binding
assembly (`bindings_for`, `session_bindings`), transcript appending,
and `turn_context`.

`bindings_for()` re-checks trust before wiring a game's own bindings.

### 4.4 `panel.py`

**What does this do?** Lets a game put its own controls beside the story
— an inventory, a spell list — without this player knowing what they are.

**What is its purpose?** Side-panel dispatch: a trusted game folder may
ship a `sidebar.py` exposing `panel_context()`, `panel_action()` and
`panel_command()`. This module locates and calls them with the same
bindings the play loop builds, and answers None when a game ships
none.

### 4.5 `plugin_sources.py`

**What does this do?** Decides whether a game's own Python is allowed to
be imported at all, which is the moment the trust decision becomes real.

**What is its purpose?** `discover_session_plugins(trusted=..., mount=...)`
assembles what `ink_engine.discovery.discover_plugins()` scans. The
engine's own plugin package is always scanned; the game folder is scanned
only when trusted.

Trust-gating has no home inside `ink_engine`, which never sees this
application's trust store — which is why this module exists here.

### 4.6 `settings_store.py`

**What does this do?** Remembers what the player has already agreed to and
preferred, so neither is asked again on the next launch.

**What is its purpose?** A small local JSON store.
`default_settings_path()` resolves the config location; the rest reads and
writes it: `load_settings`, `save_settings`, `is_folder_trusted`,
`mark_folder_trusted`, `is_strict_externals`, `set_strict_externals`,
`get_reader_prefs`, `set_reader_prefs`.

### 4.7 `main.py`

**What does this do?** Starts the application: opens the window the
player sees.

**What is its purpose?** The entry point. Wires `PlayerAPI` to one window
loading `if_player/shell/`, and is the only caller of
`webview.create_window()`/`webview.start()`.

---

## 7. Trust: the one security decision

A game folder's Python is imported only after explicit, per-folder consent.

1. `is_trusted(game_dir)` reads the stored decision.
2. Without one, the UI asks; `confirm_trust(game_dir)` records the answer.
3. `discover_session_plugins(trusted=False, ...)` scans **only** the
   engine's plugin package — the folder is never imported.
4. `bindings_for()` checks again before wiring a game's bindings.

An untrusted game still plays. It loses its own plugins and side panel, and
`PLUGIN_DENIED_SCREEN` in its manifest is what explains that to the player.

---

## 9. Module Structure Summary

```
if_player/
    __init__.py
    main.py               # entry point; the only create_window()/start() caller
    player_api.py         # PlayerAPI — the js_api surface (17 public methods)
    save_slots_api.py     # SaveSlotsAPI mixin — save slots, quicksave, import/export
    game_session.py       # the per-turn play loop; no pywebview import
    panel.py              # dispatch into a game's own sidebar.py
    plugin_sources.py     # trust-gated plugin discovery
    settings_store.py     # local JSON: trust decisions, reader preferences
    shell/                # static HTML/CSS/JS, calls window.pywebview.api.* only
tests/
```

---

## 10. Future Ideas

- **A second window.** Everything below `PlayerAPI` is window-agnostic, so a
  second window is a `main.py` change. Nothing has needed one.
- **Bundles as the normal case.** `pick_game_bundle` exists beside
  `pick_game_folder`; whether a bundle becomes the default input is open.
- **Sharing the panel contract.** `panel.py`'s three-function protocol is
  this player's own. If another application wants the same side panel, the
  contract belongs in `ink_engine` — the tension is that it describes a user
  interface, and the engine holds no opinions about one.
