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
tag_name = "v3.1.3"
release_title = "MusicLinker v3.1.3 — Auto DB Repair, Real-Time Audio Swap Progress & Background Mini-Dock, Album Inconsistency Resolver"
release_body = """### MusicLinker v3.1.3 Release Notes

#### 1. Automated 1-Click Database Repair & Health Optimization
- **Deep SQLite Auto-Repair**: Added a dedicated **"Auto-Fix DB"** tool accessible in the Settings / Storage pane and via `POST /api/library/auto-fix`.
- **Pruning & Linking**: Automatically prunes orphaned track records whose files no longer exist on disk, scans download directories to re-index missing files, repairs zero/missing durations and bitrates directly from MP3 headers using Mutagen, auto-links matching `.lrc` lyric files, cleans stray whitespace in artist and album tags, and runs SQLite `PRAGMA optimize`.

#### 2. Audio Version Substitution: Real-Time Progress & Background Mini-Dock
- **Live Staged Progress Telemetry**: Audio swapping now features a real-time progress bar (0% -> 100%) tracking pipeline stages: connecting to stream, downloading audio stream with byte progress, extracting MP3, and rewriting ID3 tags with preserved cover art.
- **Background Mini-Dock Pill**: Users can now safely minimize the swap modal (`btn-minimize-swap-modal` or "Run in Background") or close it without aborting the swap. A floating bottom-right dock pill (`#floating-swap-pill`) stays visible showing live percentage and title, allowing instant one-click restoration to the modal.
- **Fixed `this.loadTrack` Bug**: Fixed an issue where swap completion threw `TypeError: this.loadTrack is not a function`, which incorrectly displayed a false failure toast despite the audio file having successfully replaced on disk.
- **Browser Audio Stream Cache-Busting**: Seamlessly reloads the swapped audio without playing stale cached bytes.

#### 3. Album Inconsistency Resolver (Cover Art, Release Year & Artist)
- **Automatic Inconsistency Detection**: New background audit scans all tracks sharing an album title to detect conflicts across embedded cover art fingerprints, conflicting release years (e.g. 2013 vs 2021), or mismatched artist tags.
- **Header Notification Pill**: A warning pill badge (`⚠️ X Conflict(s)`) dynamically surfaces in the top navigation bar whenever album conflicts are found.
- **Interactive Resolution Modal**: Dedicated modal displays canonical cover art cards, release year chips, and album artist input. Selecting a canonical master choice and clicking **"Unify All Tracks in Album"** rewrites ID3 tags on disk and synchronizes SQLite in one atomic action.

#### 4. Packaging & Binaries
- Android Native APK (`MusicLinker-v3.1.3.apk`)
- Windows x64 Portable Executable (`dist/MusicLinker.exe`)
- Windows x64 ZIP Distribution (`MusicLinker-v3.1.3-windows-x64.zip`)
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
    ("dist/MusicLinker-v3.1.3.apk", "application/vnd.android.package-archive"),
    ("dist/MusicLinker-v3.1.3-windows-x64.zip", "application/zip"),
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
            with urllib.request.urlopen(del_req) as resp:
                print(f"Deleted previous asset: {asset_name}")
        except Exception as del_err:
            print(f"Warning: could not delete {asset_name}: {del_err}")

    upload_url = f"{clean_upload_url}?name={urllib.parse.quote(asset_name)}"
    file_bytes = file_path.read_bytes()
    print(f"Uploading {asset_name} ({len(file_bytes) / (1024*1024):.2f} MB)...")

    up_req = urllib.request.Request(
        upload_url,
        data=file_bytes,
        headers={
            **headers,
            "Content-Type": mime_type,
            "Content-Length": str(len(file_bytes)),
        },
    )
    with urllib.request.urlopen(up_req) as resp:
        res_json = json.loads(resp.read().decode("utf-8"))
        print(f"Successfully uploaded: {res_json['name']} -> {res_json['browser_download_url']}")

print(f"\nAll v3.1.3 release assets successfully uploaded to {existing_release['html_url']}")
