"""Process configuration (environment) and the editable settings (``<data>/settings.json``)."""

from __future__ import annotations

import copy
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import APP_ID, DEFAULT_PORT
from .hoard_link.appconfig import AppPaths, env_flag, env_int, env_str
from .hoard_link.atomic import read_json, write_json_atomic
from .hoard_link.guard import parse_allowed_hosts
from .sources.osm import DEFAULT_SERVERS
from .worker import isolate_default

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_SETTINGS: dict[str, Any] = {
    "bed_x_mm": 256.0,             # printer bed (a Bambu Lab X1/P1 by default)
    "bed_y_mm": 256.0,
    "nozzle_mm": 0.4,
    "default_preset": "city",
    "dem": "auto",                 # auto | ign | terrarium
    "overpass_servers": list(DEFAULT_SERVERS),
    "vulcan_auto": True,           # announce finished models so the hub rule files them in Vulcan's library
    "language": "es",
    "keep_builds": 2,              # finished builds kept per project (the newest first)
    "export_dir": "",              # where export_copy writes when no folder is given (empty = Desktop/Gulliver)
    "dataforsyningen_token": "",   # free token of Denmark's data supply: its laser surface model (empty = not used)
}
SECRET_SETTINGS = {"dataforsyningen_token"}       # shown masked, never sent back in full

SETTING_TYPES = {"bed_x_mm": float, "bed_y_mm": float, "nozzle_mm": float, "default_preset": str, "dem": str,
                 "overpass_servers": list, "vulcan_auto": bool, "language": str, "keep_builds": int, "export_dir": str, "dataforsyningen_token": str}


@dataclass
class Config:
    data_dir: Path = field(default_factory=lambda: REPO_ROOT / "data")
    port: int = DEFAULT_PORT
    allowed_hosts: tuple[str, ...] = ()
    data_dir_configured: bool = False
    fake: bool = False              # demo/tests: an invented town, no network
    background: bool = True         # the job worker thread; tests may run jobs inline
    isolate: bool = False           # builds in a child process (the app does this unless in demo mode)

    @property
    def paths(self) -> AppPaths:
        return AppPaths(APP_ID, REPO_ROOT, self.data_dir, self.data_dir_configured)

    @property
    def token_path(self) -> Path:
        return self.paths.token_path

    @property
    def settings_path(self) -> Path:
        return self.data_dir / "settings.json"

    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def jobs_path(self) -> Path:
        return self.data_dir / "jobs.json"

    @classmethod
    def from_env(cls) -> "Config":
        data_env = env_str("GULLIVER_DATA_DIR")
        return cls(
            data_dir=Path(data_env).expanduser() if data_env else REPO_ROOT / "data",
            port=env_int("GULLIVER_PORT", default=DEFAULT_PORT) or DEFAULT_PORT,
            allowed_hosts=tuple(parse_allowed_hosts(os.environ.get("GULLIVER_ALLOWED_HOSTS", ""))),
            data_dir_configured=bool(data_env),
            fake=env_flag("GULLIVER_FAKE"),
            isolate=isolate_default(env_flag("GULLIVER_FAKE")),
        )


class Settings:
    """Thread-safe JSON settings with defaults; ``update`` validates and writes atomically."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        stored = read_json(path, {}) or {}
        self._data = {**copy.deepcopy(DEFAULT_SETTINGS), **{k: v for k, v in stored.items() if k in DEFAULT_SETTINGS}}

    def get(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data)

    def __getitem__(self, key: str) -> Any:
        with self._lock:
            return copy.deepcopy(self._data[key])

    def update(self, changes: dict[str, Any]) -> dict[str, Any]:
        clean: dict[str, Any] = {}
        for key, value in (changes or {}).items():
            if key not in DEFAULT_SETTINGS:
                raise ValueError(f"Unknown setting: {key}")
            kind = SETTING_TYPES[key]
            if value is None:
                clean[key] = copy.deepcopy(DEFAULT_SETTINGS[key])
                continue
            if kind is float:
                value = float(value)
                if not 20 <= value <= 2000 and key != "nozzle_mm":
                    raise ValueError(f"{key} must be between 20 and 2000 mm")
                if key == "nozzle_mm" and not 0.1 <= value <= 2.0:
                    raise ValueError("nozzle_mm must be between 0.1 and 2")
            elif kind is int:
                value = int(value)
                if key == "keep_builds" and not 1 <= value <= 20:
                    raise ValueError("keep_builds must be between 1 and 20")
            elif kind is bool:
                value = bool(value)
            elif kind is list:
                if not isinstance(value, list) or not all(isinstance(v, str) and v.startswith("http") for v in value) or not value:
                    raise ValueError(f"{key} must be a list of URLs")
            else:
                value = str(value).strip()
                if key == "dem" and value not in ("auto", "ign", "terrarium"):
                    raise ValueError("dem must be auto, ign or terrarium")
                if key == "default_preset" and value not in ("city", "landscape", "relief"):
                    raise ValueError("default_preset must be city, landscape or relief")
                if key == "language" and value not in ("es", "en"):
                    raise ValueError("language must be es or en")
            clean[key] = value
        with self._lock:
            self._data.update(clean)
            write_json_atomic(self.path, self._data)
            return copy.deepcopy(self._data)
