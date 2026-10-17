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
tag_name = "v3.1.0"
release_title = "MusicLinker v3.1.0 — Audio Version Swapping, 15s Snippet Previews, Standalone Mobile App UX & Bulk Album Grouping"
release_body = """### MusicLinker v3.1.0 Release Notes

#### 1. Audio Version Swapper & 15-Second Snippet Previews
- Added a dedicated version changer button on every track card and within the track metadata editor.
- Quickly re-searches alternative audio versions on YouTube (matching title, artist, and duration comparisons).
- Real-time 15-second snippet preview playback from the start of the audio to verify accuracy before swapping.
- Replaces audio file on disk while permanently preserving existing ID3v2.4 tags, album covers, and synchronized lyrics.

#### 2. Standalone Phone / Android APK Navigation Overhaul
- Complete mobile UI redesign providing a native, standalone app feel on smartphones and Android APK (`< 768px`).
- Fixed bottom navigation bar with 5 primary sections: **Library**, **Downloader**, **Lyrics**, **Studio / EQ**, and **Settings**.
- Persistent docked mini-player floating directly above the bottom bar with progress line, instant play/pause, and tap-to-expand Big Player modal.
- Resolved APK HTML fallback `unexpected token '<'` error with automatic connection health detection and quick-connect modal.

#### 3. Bulk Multi-Select & Official Album Tracklist Organization
- Select tracks via checkboxes or Ctrl+Click with a floating action bar (`X selected`).
- Group selected tracks into a unified canonical album.
- Match against official Spotify and MusicBrainz tracklists (`1..N`) with confidence ratings and editable track number selectors.
- Applies album metadata, updates ID3 tags, and organizes files into standard folder structures in one click.

#### 4. Spotify Scraper Hardening
- Added embed URL and track parsing resilience (`open.spotify.com/embed/...`).
- Query parameter stripping (`?si=...`) and dual JSON/OpenGraph fallback parsing.
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
    ("MusicLinker-v3.1.0.apk", "application/vnd.android.package-archive"),
    ("MusicLinker-v3.1.0-windows-x64.zip", "application/zip"),
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
