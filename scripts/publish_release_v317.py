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
tag_name = "v3.1.7"
release_title = "MusicLinker v3.1.7 — Long-Press Selection, Custom Right-Click Context Menu, Batch Modify & Bulk Delete"
release_body = """### MusicLinker v3.1.7 Release Notes

#### 1. Long-Press Checkbox Selection & Modern Multi-Select UX
- **Touch & Mouse Long-Press (420ms)**: Holding down on any track row automatically activates selection mode, provides subtle haptic vibration, and selects the long-pressed track.
- **Full-Row Touch Toggling**: In selection mode, tapping anywhere on a track row toggles selection without initiating playback, eliminating the need to tap tiny checkboxes on mobile screens.
- **Desktop Hover Affordance**: On desktop, hovering over track rows reveals an interactive checkbox allowing instant selection with a single click.
- **Enhanced Floating Multi-Select Dock**: Elevated backdrop-blur toolbar displaying live count pills, `Select All`, `Clear`, `▶ Play Selection`, `✏️ Batch Modify`, `🗂️ Group Album`, `➕ Playlist`, `🗑️ Delete`, and `✕ Exit (Esc)`.

#### 2. Sleek Custom Right-Click Context Menu
- **Native-Like Desktop Precision**: Right-clicking any track row reveals a dark-themed floating context menu at the cursor with viewport boundary detection.
- **Rich Context Actions**:
  - 🎵 **Play Now**, ⏭️ **Play Next**, ➕ **Add to Queue**
  - ✏️ **Edit Metadata** (single track) or **Batch Modify** (if multi-selected)
  - 🗂️ **Group into Album...** (official Spotify/MusicBrainz tracklist matching)
  - ➕ **Add to Playlist...**
  - 🔄 **Change Version / Swap Audio...** (candidate preview audition & swap)
  - 📥 **Save on Device (APK Cache)**
  - 📋 **Copy Track Info ("Artist - Title")** & 🔗 **Copy Stream URL**
  - 🗑️ **Delete Track(s)...**
  - ☑️ **Select / Deselect**

#### 3. Batch Modify Engine (`POST /api/tracks/batch-modify`)
- **Common Metadata Overrides**: Set new Artist, Album, or Release Year across all selected tracks simultaneously while keeping other tags intact.
- **Find & Replace**: Regex-powered search & replace across Title, Artist, Album, or All fields (e.g. effortlessly strip `[Official Video]` or `(Remastered)`).
- **Text Case Formatting**: Convert titles or tags to `Title Case`, `UPPERCASE`, or `lowercase`.
- **Live Before & After Diff Preview**: Real-time table showing exactly what fields will change before committing.
- **Atomic File Renaming & Tagging**: Automatically rewrites ID3 tags, moves/renames files and directories, and updates SQLite.

#### 4. Dedicated Bulk Delete Confirmation Modal
- **Replaced Browser Alert**: Removed native `confirm()` popups in favor of an informative confirmation modal.
- **Track List Preview**: Displays thumbnails, titles, and artists of all items queued for deletion.
- **Configurable Options**:
  - Toggle whether to permanently delete audio (`.mp3`) and lyrics (`.lrc`) files from disk.
  - Toggle whether to clear hashes from download history to allow future re-downloads.

#### 5. Binaries & Packages
- Android Native APK (`MusicLinker-v3.1.7.apk`)
- Windows x64 ZIP Distribution (`MusicLinker-v3.1.7-windows-x64.zip`)
- Windows x64 Standalone Executable (`dist/MusicLinker.exe`)
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
    ("dist/MusicLinker-v3.1.7.apk", "application/vnd.android.package-archive"),
    ("dist/MusicLinker-v3.1.7-windows-x64.zip", "application/zip"),
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
        asset_id = existing_assets[asset_name]
        print(f"Asset {asset_name} already exists (id={asset_id}), deleting prior to re-upload...")
        del_url = f"https://api.github.com/repos/{repo}/releases/assets/{asset_id}"
        del_req = urllib.request.Request(del_url, headers=headers, method="DELETE")
        try:
            with urllib.request.urlopen(del_req) as resp:
                pass
        except Exception as e:
            print(f"Failed to delete {asset_name}: {e}")

    print(f"Uploading {asset_name} ({file_path.stat().st_size / (1024*1024):.2f} MB)...")
    with open(file_path, "rb") as f:
        data = f.read()

    upload_url = f"{clean_upload_url}?name={urllib.parse.quote(asset_name)}"
    up_req = urllib.request.Request(
        upload_url,
        data=data,
        headers={
            **headers,
            "Content-Type": mime_type,
            "Content-Length": str(len(data)),
        },
    )
    try:
        with urllib.request.urlopen(up_req) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            print(f"Uploaded {asset_name} successfully -> {res.get('browser_download_url')}")
    except Exception as e:
        print(f"Failed to upload {asset_name}: {e}")

print(f"\nAll release tasks completed for {tag_name}!")
