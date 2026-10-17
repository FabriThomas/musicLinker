import json
import os
from pathlib import Path
import subprocess
import urllib.error
import urllib.parse
import urllib.request

# 1. Fetch token from git credential manager
p = subprocess.Popen(
    ["git", "credential", "fill"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
)
out, _ = p.communicate("protocol=https\nhost=github.com\n\n")
creds = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
token = creds.get("password")

if not token:
    raise RuntimeError("Could not retrieve GitHub token from git credential helper.")

headers = {
    "Authorization": f"token {token}",
    "Accept": "application/vnd.github.v3+json",
    "User-Agent": "MusicLinker-Release-Publisher",
}

repo = "FabriThomas/musicLink"
tag_name = "v3.1.5"
release_title = "MusicLinker v3.1.5 — Fixed Album Inconsistency Selection & Cover Sync, Resolved Audio Substitution Method Crash"
release_body = """### MusicLinker v3.1.5 Release Notes

#### 1. Album Inconsistency Resolver Fixes
- **Interactive Option Selection**: Resolved UI bug where clicking on an alternative album cover or year card in the inconsistency modal immediately reset selection back to the first option due to state overwrites on re-render.
- **Permanent Cover Art Replacement**: Fixing album inconsistencies now completely updates and rewrites cover art across all member tracks on disk (`ID3v2.4 APIC`) and SQLite (`has_cover = 1`).
- **Custom Cover Upload**: Added file upload support directly inside the inconsistency modal so users can upload their own high-res cover image (`.jpg`, `.png`, `.webp`) to canonicalize the entire album.
- **Cache-Busting & Live Feedback**: Resolved 24-hour browser cover caching by switching to `Cache-Control: no-cache, must-revalidate` with `ETag` validation and appending version query parameters so updated covers reflect instantly across the player and library views without reloading.

#### 2. Audio Substitution Engine ("Select & Swap") Fixes
- **Backend Tagging Method Crash**: Fixed `AttributeError: 'AudioTagger' object has no attribute 'tag_mp3'` that caused the backend to crash after downloading reached 100%, leaving the original audio file unchanged and returning HTTP 500.
- **ID3 Version Fallback**: Added robust ID3v2.0/v2.2 corrupt header stripping via mutagen before tag serialization, eliminating `ID3UnsupportedVersionError`.
- **Immediate Playback & Cache Eviction**: Fixed stale playback where `offlineStore` (CacheStorage/IndexedDB) continued serving cached audio blobs after a swap; now properly evicts and re-caches the swapped track and updates the active play queue.

#### 3. Packaging & Binaries
- Android Native APK (`MusicLinker-v3.1.5.apk`)
- Windows x64 Portable Executable (`dist/MusicLinker.exe`)
- Windows x64 ZIP Distribution (`MusicLinker-v3.1.5-windows-x64.zip`)
"""

# 2. Check if release already exists, or create new release
release_url = f"https://api.github.com/repos/{repo}/releases/tags/{tag_name}"
existing_release = None
try:
    req = urllib.request.Request(release_url, headers=headers)
    with urllib.request.urlopen(req) as resp:
        existing_release = json.loads(resp.read().decode("utf-8"))
        print(f"Found existing release for {tag_name} (id={existing_release['id']})")
except urllib.error.HTTPError as err:
    if err.code != 404:
        raise

if not existing_release:
    create_url = f"https://api.github.com/repos/{repo}/releases"
    payload = {
        "tag_name": tag_name,
        "target_commitish": "main",
        "name": release_title,
        "body": release_body,
        "draft": False,
        "prerelease": False,
    }
    req = urllib.request.Request(
        create_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        existing_release = json.loads(resp.read().decode("utf-8"))
        print(f"Created new GitHub release {tag_name} (id={existing_release['id']})")

upload_url_template = existing_release["upload_url"]
clean_upload_url = upload_url_template.split("{")[0]

# 3. Upload assets
assets_to_upload = [
    ("dist/MusicLinker-v3.1.5.apk", "application/vnd.android.package-archive"),
    ("dist/MusicLinker-v3.1.5-windows-x64.zip", "application/zip"),
    ("dist/MusicLinker.exe", "application/octet-stream"),
]

existing_assets = {a["name"]: a["id"] for a in existing_release.get("assets", [])}

for file_path_str, mime_type in assets_to_upload:
    file_path = Path(file_path_str)
    if not file_path.exists():
        print(f"Warning: file {file_path} not found, skipping.")
        continue

    asset_name = file_path.name
    if asset_name in existing_assets:
        delete_url = f"https://api.github.com/repos/{repo}/releases/assets/{existing_assets[asset_name]}"
        del_req = urllib.request.Request(delete_url, headers=headers, method="DELETE")
        try:
            with urllib.request.urlopen(del_req) as del_resp:
                print(f"Deleted old asset {asset_name} (id={existing_assets[asset_name]})")
        except Exception as e:
            print(f"Could not delete old asset {asset_name}: {e}")

    upload_endpoint = f"{clean_upload_url}?name={urllib.parse.quote(asset_name)}"
    with open(file_path, "rb") as f:
        data = f.read()

    upload_headers = {
        **headers,
        "Content-Type": mime_type,
        "Content-Length": str(len(data)),
    }
    up_req = urllib.request.Request(upload_endpoint, data=data, headers=upload_headers)
    try:
        with urllib.request.urlopen(up_req) as up_resp:
            print(f"Successfully uploaded {asset_name} ({len(data)} bytes) to release {tag_name}!")
    except Exception as e:
        print(f"Failed uploading {asset_name}: {e}")
