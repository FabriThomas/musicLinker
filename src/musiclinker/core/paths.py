"""Centralized, PyInstaller-aware filesystem path resolution."""

from __future__ import annotations

import os
import sys
from pathlib import Path


class AppPaths:
    """Resolves application directories across source execution and PyInstaller frozen builds."""

    def __init__(self, base_dir: Path | None = None) -> None:
        if base_dir is not None:
            self.root_dir = base_dir.resolve()
        elif getattr(sys, "frozen", False):
            self.root_dir = Path(sys.executable).resolve().parent
        else:
            self.root_dir = Path(__file__).resolve().parents[3]

        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            self.bundle_dir = Path(getattr(sys, "_MEIPASS")).resolve()
            self.web_dir = self.bundle_dir / "musiclinker" / "web"
        else:
            self.bundle_dir = Path(__file__).resolve().parents[1]
            self.web_dir = self.bundle_dir / "web"

        self.app_data_dir = self.root_dir / ".app_data"
        self.webview_cache_dir = self.app_data_dir / "webview_cache"
        self.logs_dir = self.app_data_dir / "logs"
        self.downloads_dir = self.root_dir / "downloads"
        self.staging_dir = self.downloads_dir / ".staging"
        self.excess_lists_dir = self.downloads_dir / "_excess_lists"

        self.db_path = self.root_dir / "library.db"
        self.legacy_config_path = self.root_dir / "config.json"
        self.legacy_hashes_path = self.root_dir / "seen_hashes.json"
        self.host_config_path = self.root_dir / "host_config.json"
        self.env_path = self.root_dir / ".env"
        self.icon_ico_path = self._resolve_asset("icon.ico")
        self.icon_png_path = self._resolve_asset("icon.png")

    def _resolve_asset(self, filename: str) -> Path:
        candidate_root = self.root_dir / filename
        if candidate_root.exists():
            return candidate_root
        return self.bundle_dir / filename

    def ensure_directories(self) -> None:
        """Create required runtime directories if they do not exist."""
        for directory in (
            self.app_data_dir,
            self.webview_cache_dir,
            self.logs_dir,
            self.downloads_dir,
            self.staging_dir,
            self.excess_lists_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


PATHS = AppPaths(
    Path(os.environ["MUSICLINKER_HOME"]) if "MUSICLINKER_HOME" in os.environ else None
)
PATHS.ensure_directories()