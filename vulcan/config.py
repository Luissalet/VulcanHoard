"""Process-level configuration read from the environment (never from the DB)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .guard import parse_allowed_hosts

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PORT = 5186
DEFAULT_MAX_FILE_MB = 300
DEFAULT_THUMB_SIZE = 512


def default_workers() -> int:
    return max(1, (os.cpu_count() or 2) - 2)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int(raw: str, default: int, low: int, high: int) -> int:
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if low <= value <= high else default


@dataclass
class Config:
    """Everything the process needs before the database exists."""

    data_dir: Path = field(default_factory=lambda: REPO_ROOT / "data")
    port: int = DEFAULT_PORT
    port_strict: bool = False
    watch: bool = True  # start watchdog observers for roots with watch=1
    autostart: bool = True  # rescan every enabled root when the app starts
    thumbnails: bool = True  # render thumbnails while scanning
    thumb_size: int = DEFAULT_THUMB_SIZE
    max_file_mb: int = DEFAULT_MAX_FILE_MB  # bigger files are listed but not parsed
    scan_workers: int = field(default_factory=default_workers)  # processes that hash/parse/render; 1 = inline
    skip_small_bytes: int = 0  # default for new roots: files under this size are not listed at all
    allowed_hosts: tuple[str, ...] = ()  # extra Host values (exact or *.suffix) besides localhost
    data_dir_configured: bool = False

    @property
    def db_path(self) -> Path:
        return self.data_dir / "vulcan-hoard.db"

    @property
    def token_path(self) -> Path:
        return self.data_dir / "mcp-token"

    @property
    def thumbs_dir(self) -> Path:
        return self.data_dir / "thumbs"

    @classmethod
    def from_env(cls) -> "Config":
        raw_dir = _env("VULCAN_DATA_DIR")
        port = _int(_env("VULCAN_PORT") or _env("PORT") or str(DEFAULT_PORT), DEFAULT_PORT, 1, 65535)
        return cls(
            data_dir=Path(raw_dir).expanduser() if raw_dir else REPO_ROOT / "data",
            port=port,
            port_strict=_env("PORT_STRICT") == "1",
            watch=_env("VULCAN_WATCH", "1") != "0",
            autostart=_env("VULCAN_AUTOSTART", "1") != "0",
            thumbnails=_env("VULCAN_THUMBS", "1") != "0",
            thumb_size=_int(_env("VULCAN_THUMB_SIZE", str(DEFAULT_THUMB_SIZE)), DEFAULT_THUMB_SIZE, 64, 2048),
            max_file_mb=_int(_env("VULCAN_MAX_FILE_MB", str(DEFAULT_MAX_FILE_MB)), DEFAULT_MAX_FILE_MB, 1, 100000),
            scan_workers=_int(_env("VULCAN_SCAN_WORKERS", str(default_workers())), default_workers(), 1, 64),
            skip_small_bytes=_int(_env("VULCAN_SKIP_SMALL_BYTES", "0"), 0, 0, 10**12),
            allowed_hosts=parse_allowed_hosts(_env("VULCAN_ALLOWED_HOSTS")),
            data_dir_configured=bool(raw_dir),
        )
