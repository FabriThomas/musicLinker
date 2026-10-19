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
tag_name = "v3.1.8"
release_title = "MusicLinker v3.1.8 — Advanced Audio Track Replacement, Multi-Step Progress Tracking & Instant Undo Restore"
release_body = """### MusicLinker v3.1.8 Release Notes

#### 1. Advanced Audio Replacement Options & Quality Settings
- **Custom Bitrate Selection**: Choose target audio encoding quality on replacement (`320 kbps Extreme / CBR`, `256 kbps High Fidelity`, `192 kbps Standard`, or `128 kbps Compact`).
- **Cover Art Preservation vs. Replacement**: Keep pristine original embedded album cover art by default, or opt to swap with the high-resolution YouTube video thumbnail.
- **Synced Lyrics Re-Alignment**: Automatically re-fetch and align timestamped `.lrc` lyrics matching the replacement version's exact audio length and cues.
- **Safety Backup Retention**: Preserves a backup of original audio (`.mp3.bak`) and lyrics (`.lrc.bak`) on disk for instant one-click restoration.

#### 2. Multi-Phase Real-Time Tracking & Telemetry
- **Granular Stepper Indicators**: Live multi-phase progress badges showing exact step execution:
  `Stream` ➔ `Download` ➔ `Swap` ➔ `ID3/Tags` ➔ `Lyrics` ➔ `Complete`.
- **Live Speed & ETA Telemetry**: Real-time throughput reporting in both modal and minimized floating pill.
- **Resilient Minimizable Execution**: Seamlessly minimize or dismiss the replacement modal while audio downloads and transcodes in the background.

#### 3. Instant Undo & Context Menu Audio Restoration
- **Toast Quick-Undo Action**: An interactive `[↺ Restore Original]` button appears on completion toasts, allowing immediate rollback if you dislike the replacement version.
- **Right-Click Context Menu Action**: If a track has an audio backup (`.mp3.bak`), a dedicated `↺ Restore Previous Audio (.bak)` option appears in the context menu.
- **Windows File-Lock Safeguard**: Automatically pauses and unhooks active audio stream handles before replacing or restoring audio files, preventing Windows `[WinError 32]` lock crashes.

#### 4. Binaries & Packages
- Android Native APK (`MusicLinker-v3.1.8.apk`)
- Windows x64 ZIP Distribution (`MusicLinker-v3.1.8-windows-x64.zip`)
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
    ("dist/MusicLinker-v3.1.8.apk", "application/vnd.android.package-archive"),
    ("dist/MusicLinker-v3.1.8-windows-x64.zip", "application/zip"),
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
