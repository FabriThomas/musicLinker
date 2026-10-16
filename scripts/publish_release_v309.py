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
tag_name = "v3.0.9"
release_title = "MusicLinker v3.0.9 — Permanent Lyrics Sync, Vector SVG UI, Hero Player & AppData Device Storage"
release_body = """### MusicLinker v3.0.9 Release Notes

#### 1. Permanent Lyrics Alteration via Sync Controls (-0.5s / +0.5s)
- Adjusting sync using `-0.5s` and `+0.5s` permanently shifts the `.lrc` timestamp lines and updates the ID3v2.4 `USLT` frame directly inside the MP3 file on disk.
- Automatically syncs and updates cached offline copies in IndexedDB / CacheStorage so offline playback maintains permanently shifted lyrics.
- Real-time save confirmation toast and badges (`Saved permanently ✓`) in both Studio and Big Player viewports.

#### 2. Vector SVG Visual Iconography
- Replaced emoji characters across buttons, headers, cards, modals, selectors, and controls with crisp, high-resolution SVG vector iconography.
- Refined status badges, download progress bars, playback buttons, and curation items with consistent modern visual language.

#### 3. Redesigned Hero Player & Bottom Dock
- Enlarged bottom player console with tactile drop shadows and obsidian glassmorphism.
- 56px hero play button with hover elevation and active press animations.
- Thicker 8px progress slider, larger album artwork (64px), tactile navigation controls, mute toggle button with real-time percentage badge, and instant Big Player expansion.

#### 4. AppData & Device Storage Persistence
- Downloads are now stored in persistent system AppData (`%APPDATA%\\MusicLinker\\downloads` on Windows and platform equivalents), protecting user music collections from working directory purges.
- Automatically detects and syncs legacy downloads seamlessly.
- Device storage info panel in Settings with active path display and one-click "Open Folder" explorer integration.
- Automatic caching of newly completed downloads directly into device offline storage on Android APK and browser clients.
"""

# 2. Check if release already exists, or create new release
release_url = f"https://api.github.com/repos/{repo}/releases/tags/{tag_name}"
existing_release = None
try:
    req = urllib.request.Request(release_url, headers=headers)
    with urllib.request.urlopen(req) as resp:
        existing_release = json.loads(resp.read().decode())
        print(f"Found existing release ID {existing_release['id']}")
except urllib.error.HTTPError as err:
    if err.code != 404:
        raise

if not existing_release:
    create_url = f"https://api.github.com/repos/{repo}/releases"
    payload = json.dumps({
        "tag_name": tag_name,
        "target_commitish": "main",
        "name": release_title,
        "body": release_body,
        "draft": False,
        "prerelease": False,
    }).encode("utf-8")
    req = urllib.request.Request(create_url, data=payload, headers={**headers, "Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        existing_release = json.loads(resp.read().decode())
        print(f"Created new GitHub Release: {existing_release['html_url']} (ID: {existing_release['id']})")

release_id = existing_release["id"]
upload_base = f"https://uploads.github.com/repos/{repo}/releases/{release_id}/assets"
existing_assets = {a["name"]: a["id"] for a in existing_release.get("assets", [])}

# 3. Upload assets
assets_to_upload = [
    ("MusicLinker-v3.0.9.apk", "application/vnd.android.package-archive"),
    ("MusicLinker-v3.0.9-windows-x64.zip", "application/zip"),
    ("dist/MusicLinker.exe", "application/vnd.microsoft.portable-executable"),
]

for file_path_str, mime in assets_to_upload:
    p_file = Path(file_path_str)
    if not p_file.exists():
        print(f"WARNING: File {p_file} not found! Skipping.")
        continue
    asset_name = p_file.name

    # Delete existing asset if it exists
    if asset_name in existing_assets:
        del_asset_id = existing_assets[asset_name]
        print(f"Deleting existing asset {asset_name} (ID {del_asset_id})...")
        del_req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/releases/assets/{del_asset_id}",
            headers=headers,
            method="DELETE",
        )
        urllib.request.urlopen(del_req)

    print(f"Uploading {asset_name} ({p_file.stat().st_size} bytes)...")
    with open(p_file, "rb") as f:
        data = f.read()

    encoded_name = urllib.parse.quote(asset_name)
    up_url = f"{upload_base}?name={encoded_name}"
    req = urllib.request.Request(
        up_url,
        data=data,
        headers={**headers, "Content-Type": mime},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        res = json.loads(resp.read().decode())
        print(f"Successfully uploaded {asset_name}! Asset ID: {res.get('id')}")

print("\nAll assets uploaded successfully! Release URL:", existing_release.get("html_url"))
