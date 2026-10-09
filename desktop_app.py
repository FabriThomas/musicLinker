import os
import sys
import threading
import time
import uvicorn
import webview
import pystray
from PIL import Image

from server import app as fastapi_app, get_local_ip
from config import load_config
from library import scan_library

# Ensure safe console streams
if sys.stdout is not None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr is not None:
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Global references
window_ref = None
tray_icon_ref = None
server_thread = None

def get_base_dir() -> str:
    """Get root directory whether running as script or PyInstaller bundle."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def run_server(port: int):
    """Run FastAPI audio & sync server."""
    try:
        config = uvicorn.Config(
            fastapi_app,
            host="0.0.0.0",
            port=port,
            log_level="warning",
            access_log=False
        )
        server = uvicorn.Server(config)
        server.run()
    except Exception as e:
        print(f"[Server Warning] {e}")

def get_tray_icon_image():
    """Load or generate the 64x64 RGBA system tray icon."""
    base_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    icon_path = os.path.join(base_dir, "icon.png")
    if os.path.exists(icon_path):
        try:
            return Image.open(icon_path)
        except Exception:
            pass

    # Fallback clean red/dark music note icon
    img = Image.new("RGBA", (64, 64), color=(0, 0, 0, 0))
    from PIL import ImageDraw
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([(4, 4), (60, 60)], radius=14, fill=(24, 24, 27, 255))
    draw.ellipse([(14, 14), (50, 50)], fill=(229, 9, 20, 60))
    draw.rectangle([(38, 16), (42, 38)], fill=(229, 9, 20, 255))
    draw.rectangle([(22, 22), (26, 44)], fill=(229, 9, 20, 255))
    draw.polygon([(22, 22), (42, 16), (42, 22), (22, 28)], fill=(229, 9, 20, 255))
    draw.ellipse([(16, 40), (28, 50)], fill=(229, 9, 20, 255))
    draw.ellipse([(32, 34), (44, 44)], fill=(229, 9, 20, 255))
    return img

def setup_tray_supplement():
    """
    Launch the system tray icon as a supplement to the main desktop window.
    Provides quick playback shortcuts, show/hide, and exit.
    """
    global tray_icon_ref, window_ref

    image = get_tray_icon_image()

    def action_play_pause(icon, item):
        if window_ref:
            try:
                window_ref.evaluate_js("togglePlay()")
            except Exception:
                pass

    def action_next(icon, item):
        if window_ref:
            try:
                window_ref.evaluate_js("nextTrack()")
            except Exception:
                pass

    def action_prev(icon, item):
        if window_ref:
            try:
                window_ref.evaluate_js("prevTrack()")
            except Exception:
                pass

    def action_show_window(icon, item):
        if window_ref:
            try:
                window_ref.show()
                window_ref.restore()
            except Exception:
                pass

    def action_minimize_to_tray(icon, item):
        if window_ref:
            try:
                window_ref.hide()
            except Exception:
                pass

    def action_exit(icon, item):
        try:
            if tray_icon_ref:
                tray_icon_ref.stop()
        except Exception:
            pass
        try:
            if window_ref:
                window_ref.destroy()
        except Exception:
            pass
        os._exit(0)

    menu = pystray.Menu(
        pystray.MenuItem("Play / Pause", action_play_pause),
        pystray.MenuItem("Next Track", action_next),
        pystray.MenuItem("Previous Track", action_prev),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open MusicLinker", action_show_window, default=True),
        pystray.MenuItem("Minimize to Tray", action_minimize_to_tray),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Exit", action_exit)
    )

    tray_icon_ref = pystray.Icon("musiclinker", image, "MusicLinker Player", menu)
    # run_detached runs in a clean background thread without interfering with main window
    tray_icon_ref.run_detached()

def main():
    global window_ref, server_thread

    cfg = load_config()
    port = cfg.get("server_port", 8000)
    local_ip = get_local_ip()

    print("=" * 65)
    print("   [MusicLinker] Native Desktop App with Tray Controls")
    print("=" * 65)
    print(f"  * Local PC URL     : http://localhost:{port}")
    print(f"  * Mobile Wi-Fi URL : http://{local_ip}:{port}")
    print("  * Scanning downloads library...")
    scan_res = scan_library("downloads")
    print(f"  * Synced {scan_res['scanned']} audio files.")
    print("  * Starting background server and desktop window...")
    print("=" * 65)

    # 1. Start Server in background daemon thread
    server_thread = threading.Thread(target=run_server, args=(port,), daemon=True)
    server_thread.start()

    # 2. Start System Tray Icon as a background supplement
    setup_tray_supplement()

    # Brief pause to ensure local server accepts connections
    time.sleep(0.8)

    # 3. Create Standalone Desktop App Window
    base_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    icon_path = os.path.join(base_dir, "icon.png")
    app_data_dir = os.path.join(get_base_dir(), ".app_data")
    os.makedirs(app_data_dir, exist_ok=True)

    window_ref = webview.create_window(
        title="MusicLinker Player",
        url=f"http://localhost:{port}",
        width=1180,
        height=750,
        min_size=(820, 520),
        background_color="#0c0c0e",
        text_select=False
    )

    def on_window_closed():
        # When user closes window, stop tray and exit
        try:
            if tray_icon_ref:
                tray_icon_ref.stop()
        except Exception:
            pass
        os._exit(0)

    window_ref.events.closed += on_window_closed

    # Start pywebview main GUI loop with persistent storage path (prevents WebView2 E_ABORT)
    webview.start(
        private_mode=False,
        storage_path=app_data_dir,
        icon=icon_path if os.path.exists(icon_path) else None
    )

if __name__ == "__main__":
    main()
