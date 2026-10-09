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

# Global state
server_started = threading.Event()
window_ref = None
tray_icon_ref = None
is_quitting = False

def run_server(port: int):
    config = uvicorn.Config(
        fastapi_app,
        host="0.0.0.0",
        port=port,
        log_level="warning",
        access_log=False
    )
    server = uvicorn.Server(config)
    server_started.set()
    server.run()

def get_tray_icon_image():
    base_dir = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    icon_path = os.path.join(base_dir, "icon.png")
    if os.path.exists(icon_path):
        return Image.open(icon_path)
    # Fallback blank image
    return Image.new("RGBA", (64, 64), color=(229, 9, 20, 255))

def setup_tray():
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

    def action_toggle_show(icon, item):
        if window_ref:
            try:
                window_ref.show()
                window_ref.restore()
            except Exception:
                pass

    def action_hide(icon, item):
        if window_ref:
            try:
                window_ref.hide()
            except Exception:
                pass

    def action_exit(icon, item):
        global is_quitting
        is_quitting = True
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
        pystray.MenuItem("▶ / ⏸  Play / Pause", action_play_pause),
        pystray.MenuItem("⏭  Next Track", action_next),
        pystray.MenuItem("⏮  Previous Track", action_prev),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("🗔  Show Window", action_toggle_show, default=True),
        pystray.MenuItem("🔽  Minimize to Tray", action_hide),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("❌  Exit", action_exit)
    )

    tray_icon_ref = pystray.Icon("musiclinker", image, "MusicLinker Player", menu)
    tray_icon_ref.run()

def on_closing():
    """When user clicks [X] on window, minimize to tray instead of quitting."""
    global is_quitting, window_ref
    if is_quitting:
        return True
    if window_ref:
        window_ref.hide()
    # Return False to cancel the window destruction and keep it running in tray
    return False

def main():
    global window_ref
    cfg = load_config()
    port = cfg.get("server_port", 8000)
    local_ip = get_local_ip()

    print("=" * 65)
    print("   🎵 MusicLinker: Native Desktop App with System Tray")
    print("=" * 65)
    print(f"  * Local PC URL      : http://localhost:{port}")
    print(f"  * Mobile Wi-Fi URL  : http://{local_ip}:{port}")
    print("  * Scanning downloads library...")
    scan_res = scan_library("downloads")
    print(f"  * Synced {scan_res['scanned']} audio files.")
    print("  * Launching Desktop App & System Tray Icon...")
    print("=" * 65)

    # 1. Start Personal Server in background thread
    server_thread = threading.Thread(target=run_server, args=(port,), daemon=True)
    server_thread.start()

    # 2. Start System Tray Icon in background thread
    tray_thread = threading.Thread(target=setup_tray, daemon=True)
    tray_thread.start()

    # Wait for server to initialize
    time.sleep(1.0)

    # 3. Create Native Desktop Window
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png")
    window_ref = webview.create_window(
        title="MusicLinker Player",
        url=f"http://localhost:{port}",
        width=1150,
        height=740,
        min_size=(800, 520),
        background_color="#0c0c0e",
        text_select=False
    )

    # Intercept window close to minimize to tray
    window_ref.events.closing += on_closing

    # Start pywebview main GUI loop
    webview.start(icon=icon_path if os.path.exists(icon_path) else None)

if __name__ == "__main__":
    main()
