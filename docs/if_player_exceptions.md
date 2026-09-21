# if_player — Exception Taxonomy

**Companion to:** [`if_player_design.md`](if_player_design.md)  
**Author:** Benjamin Schollnick  

**Date Created:** 2026-09-21  
**Last Updated:** 2026-09-21  
**Last Reviewed:** 2026-09-21

## What this is

Every exception this application catches, where it catches it, and what it
does with it. `if_player` raises almost nothing of its own: the exceptions
it handles come from `ink_engine`, `if_session`, and the standard library,
and it converts them into `{"error": "<message>"}` dictionaries for the
JavaScript layer.

---

## 1. Custom exception classes

| Class | Subclasses | Raised where | Caught where |
|---|---|---|---|
| `GameSessionError` | `Exception` | nowhere | nowhere |

`GameSessionError` is declared at `game_session.py:49` and is never raised,
caught, exported or tested. It is dead code, documented here because a
reader who greps for it will otherwise assume it is live.

---

## 2. The conversion boundary

The two API modules catch; nothing below them does.

| Module | Catch sites | `{"error": ...}` returns |
|---|---|---|
| `player_api.py` | 7 | 15 |
| `save_slots_api.py` | 11 | 28 |
| `game_session.py` | 1 | 0 |
| `panel.py` | 1 | 0 |
| `settings_store.py` | 1 | 0 |
| `plugin_sources.py` | 0 | 0 |

An exception never reaches JavaScript. A method either returns its result
or returns `{"error": "<message>"}`, which is why the shell has no error
handler of its own.

---

## 3. Exceptions from `ink_engine`

### `GameSourceError`

Raised by `ink_engine.game_source` when a path is neither a game folder nor
a readable bundle.

- `player_api.py:297` — `open_game()`: returns `{"error": str(error)}`.
- `player_api.py:354` — `start_new_game()`: same.

### `GameFolderError`

Raised by `ink_engine.game_folder` when a manifest is missing or malformed.

- `player_api.py:408` — returns `{"error": str(error)}`.

### `UnboundExternalError`

Raised by the engine when a story calls an `EXTERNAL` with no binding and
no Ink fallback, and strict externals are on.

- `player_api.py:414` — returns `{"error": str(error)}`.
- `player_api.py:445` — `choose()`: same, so a bad choice does not end the
  session.
- `save_slots_api.py:231` — on loading a save whose story needs a binding
  this session has not wired.

---

## 4. Exceptions from `if_session`

### `GameSaveError`

Raised by `if_session.game_saves` for an unreadable, missing or rejected
save file. The most-caught exception here, at seven sites, all in
`save_slots_api.py`: lines 281, 333, 361, 400, 459, and 308 and 475 paired
with `SaveFormatError`. Every one returns `{"error": str(error)}`.

### `SaveFormatError`

Raised by `if_session.session_state` when a save envelope's version or
fields do not match what this build writes.

- `player_api.py:410`, `save_slots_api.py:226` — returned as an error.
- `save_slots_api.py:308`, `:475` — caught together with `GameSaveError`,
  since a caller cannot act differently on the two.

---

## 5. Standard-library exceptions

### `ModuleNotFoundError` — "the game ships none"

Both sites use a failed import as the test for an optional game module,
rather than probing the filesystem first.

- `panel.py:40` — `sidebar.py` absent, so the game has no side panel:
  returns `None`.
- `game_session.py:525` — the game's own media resolver is absent: returns
  `None`.

### `OSError`, `json.JSONDecodeError`, `UnicodeDecodeError`

- `settings_store.py:61` — an unreadable or corrupt settings file returns
  `{}`, so a damaged file reads as "no settings yet" rather than stopping
  the application from starting.
- `save_slots_api.py:368`, `:389` — a save file the player picked cannot be
  read or parsed: returned as an error.

### `IndexError`

- `player_api.py:443` — `choose()` with an index the current turn does not
  offer. Returned as an error rather than raised, since the index comes
  from the UI.
