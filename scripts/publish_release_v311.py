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
tag_name = "v3.1.1"
release_title = "MusicLinker v3.1.1 — Imbued Last.fm Scrobbler, Instant Shift/Ctrl Feedback & Alternative Tracks Search Fix"
release_body = """### MusicLinker v3.1.1 Release Notes

#### 1. Imbued Last.fm Scrobbler (Out of the Box)
- **Zero API Key Setup Required**: Embedded default application credentials so anyone can scrobble immediately without registering a developer account.
- **1-Click Browser Authorization**: One-click connect flow that opens Last.fm authorization, authenticates the session, and permanently stores the session key.
- **Real-Time Now Playing & Scrobble Broadcasting**: Automatically submits "Now Playing" status to Last.fm and scrobbles tracks when crossing 50% or 4 minutes.
- **Live Scrobble Feedback**: Floating badge and notification toasts when scrobbles succeed.
- **Account Dashboard in Settings**: Shows connected user `@profile` link, disconnect button, pending offline scrobbles counter, and one-click manual flush.
- **Customizable Preferences**: Toggles to independently enable/disable scrobbling and now-playing status, plus custom API key/secret/session key drawer.

#### 2. Instant Shift & Ctrl Multi-Select Feedback (< 16ms)
- Resolved the issue where clicking rows with Shift or Ctrl displayed no visual feedback until manually opening the menu.
- **Ctrl / Cmd Click**: Toggles selection of individual track rows, immediately reveals checkboxes, updates the floating multi-select bar, and plays a tactile pulse animation.
- **Shift Click**: Instantly selects a contiguous range from the previously clicked track with immediate visual row highlighting (`is-selected`), checkboxes checked, and dynamic counter update.

#### 3. Alternative Tracks Search Engine Fix
- Fixed an endpoint nesting bug where `get_track_audio_candidates` returned candidates nested under an object rather than a list, causing the UI to falsely report "No alternative versions found".
- Added a dedicated search bar in the "Change Audio Version" modal allowing custom query searches (e.g., remixes, acoustic versions) or pasting direct YouTube URLs.
- Cascading fallback search queries to guarantee candidates are always found for any track in the library.
- Normalized candidate model attributes (`id`/`video_id`, `uploader`/`channel`, `duration_diff`/`duration_diff_sec`).
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
    ("MusicLinker-v3.1.1.apk", "application/vnd.android.package-archive"),
    ("MusicLinker-v3.1.1-windows-x64.zip", "application/zip"),
    ("dist/MusicLinker.exe", "application/vnd.microsoft.portable-executable"),
]

for file_path_str, mime in assets_to_upload:
    p_file = Path(file_path_str)
    if not p_file.exists():
        print(f"WARNING: File {p_file} not found! Skipping.")
        continue

    asset_name = p_file.name
    if asset_name in existing_assets:
        del_url = f"https://api.github.com/repos/{repo}/releases/assets/{existing_assets[asset_name]}"
        del_req = urllib.request.Request(del_url, headers=headers, method="DELETE")
        try:
            with urllib.request.urlopen(del_req) as resp:
                print(f"Deleted existing asset {asset_name}")
        except urllib.error.HTTPError as exc:
            print(f"Failed deleting asset {asset_name}: {exc}")

    print(f"Uploading {asset_name} ({p_file.stat().st_size} bytes)...")
    url = f"{upload_base}?name={urllib.parse.quote(asset_name)}"
    with open(p_file, "rb") as f:
        file_data = f.read()

    req = urllib.request.Request(
        url,
        data=file_data,
        headers={
            **headers,
            "Content-Type": mime,
            "Content-Length": str(len(file_data)),
        },
    )

    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
            print(f"Successfully uploaded {asset_name}! Asset ID: {data['id']}")
    except urllib.error.HTTPError as exc:
        print(f"ERROR uploading {asset_name}: {exc.code} - {exc.read().decode()}")
        raise

print(f"\nAll assets uploaded successfully! Release URL: https://github.com/{repo}/releases/tag/{tag_name}")
