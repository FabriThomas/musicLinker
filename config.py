import json
import os
from typing import Dict, Any

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

DEFAULT_CONFIG = {
    "lastfm_api_key": "",
    "lastfm_api_secret": "",
    "lastfm_session_key": "",
    "default_username": "",
    "min_plays": 10,
    "default_period": "12month",
    "download_cap": 25,
    "audio_quality": "192",
    "server_port": 8000,
    "output_links_file": "youtube_links.txt",
    "output_detailed_file": "songs_detailed.txt",
    "hash_store_file": "seen_hashes.json",
    "download_dir": "downloads"
}

def load_config() -> Dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                config.update(saved)
        except Exception as e:
            print(f"[Warning] Could not load config.json: {e}")

    # Environment variables override
    for env_k, cfg_k in [
        ("LASTFM_API_KEY", "lastfm_api_key"),
        ("LASTFM_API_SECRET", "lastfm_api_secret"),
        ("LASTFM_SESSION_KEY", "lastfm_session_key"),
        ("LASTFM_USERNAME", "default_username")
    ]:
        if os.environ.get(env_k):
            config[cfg_k] = os.environ[env_k]

    return config

def save_config(config: Dict[str, Any]) -> None:
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[Warning] Could not save config.json: {e}")

def get_api_credentials() -> Dict[str, str]:
    config = load_config()
    return {
        "api_key": config.get("lastfm_api_key", "").strip(),
        "api_secret": config.get("lastfm_api_secret", "").strip(),
        "session_key": config.get("lastfm_session_key", "").strip(),
        "username": config.get("default_username", "").strip()
    }
