import sys
import os

if __name__ == "__main__":
    if "--browser" in sys.argv:
        # Fallback browser mode
        import webbrowser
        import threading
        import time
        import uvicorn
        from server import app, get_local_ip
        from config import load_config
        from library import scan_library

        cfg = load_config()
        port = cfg.get("server_port", 8000)
        local_ip = get_local_ip()
        print(f"Starting server in browser mode on http://localhost:{port}...")
        scan_library("downloads")
        def open_browser():
            time.sleep(1.2)
            webbrowser.open(f"http://localhost:{port}")
        threading.Thread(target=open_browser, daemon=True).start()
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
    else:
        # Native Desktop App with Tray Controls
        from desktop_app import main
        main()
