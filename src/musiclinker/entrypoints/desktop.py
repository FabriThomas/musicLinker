"""Native Windows Desktop Application host combining FastAPI, Edge WebView2 (pywebview), and pystray System Tray."""

from __future__ import annotations

import argparse
import json
import os
import socket
import threading
import time
import webbrowser
from typing import Any

from PIL import Image, ImageDraw
import uvicorn

from musiclinker.api.app import app
from musiclinker.core.logging_config import logger
from musiclinker.core.paths import PATHS


def _Wait_for_server(host: str, port: int, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.15)
    return False


def _load_or_generate_tray_image() -> Image.Image:
    if PATHS.icon_png_path.exists():
        try:
            return Image.open(PATHS.icon_png_path).convert("RGBA")
        except Exception:
            pass
    # Fallback programmatic 64x64 RGBA icon
    img = Image.new("RGBA", (64, 64), (18, 18, 24, 255))
    draw = ImageDraw.Draw(img)
    draw.ellipse((6, 6, 58, 58), fill=(29, 185, 84, 255))
    draw.polygon([(26, 20), (26, 44), (46, 32)], fill=(18, 18, 24, 255))
    return img


def load_server_host_config() -> tuple[str, int]:
    """Load default host and port from host_config.json or .env, falling back to 0.0.0.0:8101."""
    host = "0.0.0.0"
    port = 8101

    # 1. Check host_config.json
    config_file = getattr(PATHS, "host_config_path", PATHS.root_dir / "host_config.json")
    if config_file.exists():
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "host" in data and str(data["host"]).strip():
                    host = str(data["host"]).strip()
                if "port" in data:
                    port = int(data["port"])
        except Exception as exc:
            logger.warning("Could not read host_config.json: %s", exc)

    # 2. Check .env if available
    env_file = getattr(PATHS, "env_path", PATHS.root_dir / ".env")
    if env_file.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_file)
        except Exception:
            pass

    # 3. Environment variable override
    env_host = os.environ.get("MUSICLINKER_HOST") or os.environ.get("HOST")
    if env_host and env_host.strip():
        host = env_host.strip()
    env_port = os.environ.get("MUSICLINKER_PORT") or os.environ.get("PORT")
    if env_port and env_port.strip():
        try:
            port = int(env_port.strip())
        except ValueError:
            pass

    return host, port


class DesktopAppController:
    """Coordinates the background Uvicorn thread, pystray system tray icon, and pywebview desktop window."""

    def __init__(self, host: str | None = None, port: int | None = None) -> None:
        cfg_host, cfg_port = load_server_host_config()
        self.host = host if host is not None else cfg_host
        self.port = port if port is not None else cfg_port
        display_host = "127.0.0.1" if self.host in ("0.0.0.0", "") else self.host
        self.local_url = f"http://{display_host}:{self.port}"
        self.window: Any = None
        self.tray_icon: Any = None

    def start_server_thread(self) -> None:
        config = uvicorn.Config(
            app=app,
            host=self.host,
            port=self.port,
            log_level="warning",
            access_log=False,
            log_config=None,
        )
        server = uvicorn.Server(config=config)
        thread = threading.Thread(target=server.run, name="ml_uvicorn", daemon=True)
        thread.start()
        wait_host = "127.0.0.1" if self.host in ("0.0.0.0", "") else self.host
        _Wait_for_server(wait_host, self.port)
        logger.info("MusicLinker FastAPI server listening on %s:%d", self.host, self.port)

    def _eval_js(self, script: str) -> None:
        if self.window is not None:
            try:
                self.window.evaluate_js(script)
            except Exception as exc:
                logger.debug("WebView2 JS dispatch error: %s", exc)

    def start_system_tray(self) -> None:
        try:
            import pystray

            menu = pystray.Menu(
                pystray.MenuItem("Show MusicLinker", lambda: self.window and self.window.show(), default=True),
                pystray.MenuItem("Minimize to Tray", lambda: self.window and self.window.hide()),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Play / Pause", lambda: self._eval_js("window.MusicLinkerApp?.togglePlay()")),
                pystray.MenuItem("Next Track", lambda: self._eval_js("window.MusicLinkerApp?.nextTrack()")),
                pystray.MenuItem("Previous Track", lambda: self._eval_js("window.MusicLinkerApp?.prevTrack()")),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Open in Default Browser", lambda: webbrowser.open(self.local_url)),
                pystray.MenuItem("Quit MusicLinker", self._quit_app),
            )
            self.tray_icon = pystray.Icon(
                "MusicLinker",
                _load_or_generate_tray_image(),
                "MusicLinker Personal Player",
                menu,
            )
            self.tray_icon.run_detached()
        except Exception as exc:
            logger.warning("System tray initialization skipped: %s", exc)

    def _quit_app(self) -> None:
        if self.tray_icon is not None:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
        if self.window is not None:
            try:
                self.window.destroy()
            except Exception:
                pass

    def run(self, browser_only: bool = False) -> None:
        self.start_server_thread()
        if browser_only:
            webbrowser.open(self.local_url)
            try:
                while True:
                    time.sleep(1.0)
            except KeyboardInterrupt:
                return

        self.start_system_tray()
        try:
            import webview

            self.window = webview.create_window(
                title="MusicLinker",
                url=self.local_url,
                width=1280,
                height=840,
                min_size=(940, 620),
                background_color="#09090b",
                text_select=False,
            )
            webview.start(
                private_mode=False,
                storage_path=str(PATHS.webview_cache_dir),
            )
        except Exception as exc:
            logger.warning("pywebview unavailable (%s); falling back to default web browser", exc)
            webbrowser.open(self.local_url)
            try:
                while True:
                    time.sleep(1.0)
            except KeyboardInterrupt:
                pass
        finally:
            if self.tray_icon is not None:
                try:
                    self.tray_icon.stop()
                except Exception:
                    pass


def main() -> None:
    default_host, default_port = load_server_host_config()
    parser = argparse.ArgumentParser(description="MusicLinker Desktop & Personal Server")
    parser.add_argument(
        "--host",
        type=str,
        default=default_host,
        help=f"HTTP server bind host (default from host_config.json: {default_host})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=default_port,
        help=f"HTTP server port (default from host_config.json: {default_port})",
    )
    parser.add_argument("--browser", action="store_true", help="Run in standard browser instead of WebView2 window")
    args = parser.parse_args()

    controller = DesktopAppController(host=args.host, port=args.port)
    controller.run(browser_only=args.browser)


if __name__ == "__main__":
    main()