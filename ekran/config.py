"""JSON config at ~/.config/ekran/settings.json — stdlib only, no pyxdg."""

from __future__ import annotations

import json
import os
from pathlib import Path

_DEFAULT_DIR = Path(
    os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
) / "ekran"


def _config_path() -> Path:
    return _DEFAULT_DIR / "settings.json"


def load_config() -> dict:
    path = _config_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def save_config(data: dict) -> str:
    """Write config; return error string or empty."""
    path = _config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n")
        return ""
    except OSError as e:
        return str(e)
