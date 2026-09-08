"""A small local JSON settings store -- per-folder trust decisions, and
app-wide reader preferences (font size / text width).

The trust half persists a safe-by-default, explicit-consent decision to
a plain JSON file in the OS's own per-user config directory, keyed by
each game folder's own resolved absolute path -- there is no separate
global "engine enabled" toggle, since per-folder trust is already the
complete gate.

The reader-preferences half is app-wide rather than per-game-folder --
one reader, one set of preferences, applied to every game the same way.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: The recognized font-size choices.
VALID_FONT_SIZES = {"small", "medium", "large"}

#: The recognized text-width choices.
VALID_TEXT_WIDTHS = {"narrow", "medium", "wide"}

#: The default value for both preferences when nothing has been saved yet.
DEFAULT_READER_PREF = "medium"


def default_settings_path() -> Path:
    """Return this app's own local settings file path.

    Returns:
        `~/.config/if_player/settings.json` on Linux/macOS-without-a-
        platform-specific-convention, or the platform's own per-user
        config directory equivalent where `$XDG_CONFIG_HOME` is set.
        Never created by this function -- see `load_settings()`/
        `save_settings()` for that.
    """
    import os  # pylint: disable=import-outside-toplevel

    config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(config_home) if config_home else Path.home() / ".config"
    return base / "if_player" / "settings.json"


def load_settings(settings_path: Path) -> dict[str, Any]:
    """Read the settings file, tolerating absence or corruption.

    Args:
        settings_path: The settings file's own path.

    Returns:
        The parsed settings dict, or `{}` if the file does not exist or
        cannot be parsed as JSON -- a corrupted settings file must never
        crash the player, only reset it to defaults.
    """
    if not settings_path.is_file():
        return {}
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(settings_path: Path, settings: dict[str, Any]) -> None:
    """Write the settings dict to disk, creating parent directories as needed.

    Args:
        settings_path: The settings file's own path.
        settings: The full settings dict to persist.
    """
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def is_folder_trusted(settings: dict[str, Any], game_dir: Path) -> bool:
    """Return whether `game_dir` has already been confirmed trusted.

    Args:
        settings: The loaded settings dict.
        game_dir: The game folder's own filesystem path.

    Returns:
        True only if this exact resolved path was previously recorded as
        trusted -- absent, or explicitly not-trusted, both answer False
        (safe by default).
    """
    trusted_folders = settings.get("trusted_folders", [])
    if not isinstance(trusted_folders, list):
        return False
    return str(game_dir.resolve()) in trusted_folders


def mark_folder_trusted(settings: dict[str, Any], game_dir: Path) -> dict[str, Any]:
    """Return a new settings dict with `game_dir` recorded as trusted.

    Args:
        settings: The loaded settings dict.
        game_dir: The game folder's own filesystem path.

    Returns:
        A new dict (the input is not mutated) with `game_dir`'s resolved
        path added to `trusted_folders`, deduplicated.
    """
    resolved = str(game_dir.resolve())
    existing = settings.get("trusted_folders", [])
    trusted_folders = list(existing) if isinstance(existing, list) else []
    if resolved not in trusted_folders:
        trusted_folders.append(resolved)
    return {**settings, "trusted_folders": trusted_folders}


def get_reader_prefs(settings: dict[str, Any]) -> dict[str, str]:
    """Return the reader's own font-size/text-width preferences.

    Args:
        settings: The loaded settings dict.

    Returns:
        `{"font_size", "text_width"}`, each `DEFAULT_READER_PREF`
        ("medium") unless a valid, previously-saved value exists --
        a corrupted/invalid stored value falls back to the default
        rather than surfacing.
    """
    font_size = settings.get("if_font_size")
    text_width = settings.get("if_text_width")
    return {
        "font_size": font_size if font_size in VALID_FONT_SIZES else DEFAULT_READER_PREF,
        "text_width": text_width if text_width in VALID_TEXT_WIDTHS else DEFAULT_READER_PREF,
    }


def set_reader_prefs(settings: dict[str, Any], font_size: str, text_width: str) -> dict[str, Any]:
    """Return a new settings dict with the reader's preferences updated.

    Silently ignores an unrecognized value for either field rather than
    raising -- a field simply keeps its previous value when the
    submitted one isn't valid.

    Args:
        settings: The loaded settings dict.
        font_size: The submitted font-size value.
        text_width: The submitted text-width value.

    Returns:
        A new dict (the input is not mutated), with `if_font_size`/
        `if_text_width` updated only for whichever of the two arguments
        was a real, recognized choice.
    """
    updated = dict(settings)
    if font_size in VALID_FONT_SIZES:
        updated["if_font_size"] = font_size
    if text_width in VALID_TEXT_WIDTHS:
        updated["if_text_width"] = text_width
    return updated
