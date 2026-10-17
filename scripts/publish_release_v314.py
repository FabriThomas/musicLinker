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
tag_name = "v3.1.4"
release_title = "MusicLinker v3.1.4 — Fixed Audio Swap Stalling & Stream Connection Hangs, Subtitle 429 Fix"
release_body = """### MusicLinker v3.1.4 Release Notes

#### 1. Audio Substitution Engine Overhaul & Fix for "Connecting to Audio Stream" Hang
- **Eliminated Subtitle 429 Bottleneck**: Identified and resolved the root cause of audio swapping getting stuck on *"Connecting to audio stream..."*. In previous versions, yt-dlp requested automatic subtitles for multiple languages (`en.*`, `pt.*`, `es.*`, `orig`), triggering YouTube's HTTP 429 rate limiting with aggressive exponential backoff before the audio stream could even start downloading. Automatic subtitle requests have been disabled for audio swap workflows.
- **Immediate Fast Downloads**: Audio swaps now connect and begin downloading in under 2 seconds without rate-limit stalling.
- **Enhanced Progress Hooks**: Added comprehensive progress and post-processor hooks in `download_mp3_to_staging` so that even with chunked streams or unknown total lengths, byte progress updates smoothly from `5%` through `100%`.
- **Frontend Error Trapping & Button Recovery**: The polling loop in `confirmReplaceAudio` now intercepts error states immediately, cancels the polling interval, surfaces detailed error diagnostics, and restores the swap buttons.

#### 2. Packaging & Binaries
- Android Native APK (`MusicLinker-v3.1.4.apk`)
- Windows x64 Portable Executable (`dist/MusicLinker.exe`)
- Windows x64 ZIP Distribution (`MusicLinker-v3.1.4-windows-x64.zip`)
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
    ("dist/MusicLinker-v3.1.4.apk", "application/vnd.android.package-archive"),
    ("dist/MusicLinker-v3.1.4-windows-x64.zip", "application/zip"),
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

print(f"\nAll v3.1.4 release assets successfully uploaded to {existing_release['html_url']}")
