"""`python -m vulcan` — run the app with uvicorn on 127.0.0.1 (the shared `hoard_link.service.run_main`: a second start
reports the running instance and exits before touching the data folder or the token)."""

from __future__ import annotations

from .hoard_link.service import run_main


def main() -> int:
    return run_main(service="vulcan-hoard", package="vulcan", default_port=5186, app_factory="vulcan.main:create_app",
                    data_dir_env="VULCAN_DATA_DIR", port_env="VULCAN_PORT", title="Vulcan's Hoard", open_browser_default=False)


if __name__ == "__main__":
    raise SystemExit(main())
