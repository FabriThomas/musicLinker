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
tag_name = "v3.0.8"
release_title = "MusicLinker v3.0.8 — DSP Equalizer, Tactile UI/UX, Precision Audio Matching & FIFO Fix"
release_body = """### MusicLinker v3.0.8 Release Notes

#### 1. Precision Spotify & Album Audio Matching
- Multi-factor candidate scoring evaluating title keyword recall, artist verification, and strict duration tolerance (within +/- 4-12s).
- Fixes the bug where an album track download could occasionally pull the artist's #1 hit or an unrelated video instead of the exact studio release.
- Automatically rejects candidates missing core title keywords or having massive duration mismatches.

#### 2. Web Audio API 5-Band Equalizer (DSP)
- Integrated 5-band BiquadFilter equalizer chain (60 Hz Sub-Bass, 230 Hz Bass, 910 Hz Midrange, 3.6 kHz Presence, 14 kHz Air/Treble) with real-time gain adjustment (-12 dB to +12 dB).
- Built-in sound presets: Flat, Bass Boost, Treble Boost, Rock, Electronic, Vocal, Hip-Hop, Acoustic, plus Custom tuning.
- Interactive Equalizer modal accessible from both the bottom player bar and Big Player modal, with local storage persistence and quick bypass switch.

#### 3. Tactile UI/UX Redesign & Bigger Controls
- Upgraded button design with tactile depth, layered bevel highlights, drop shadows, and active click compression.
- Redesigned player console with prominent circular Hero Play button, larger rounded navigation controls, styled seek and volume bars, and quick EQ access.
- Cleaner obsidian glassmorphism cards and panels.

#### 4. FIFO Queue Telemetry & SSE Glitch Resolution
- Resolved the visual flicker when initiating downloads back-to-back by ensuring only one active EventSource is subscribed at a time.
- Automatic FIFO queue transition: when the active download completes, the UI smoothly moves to monitor the next queued job in the buffer."""

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
    ("dist/MusicLinker-v3.0.8-android.apk", "application/vnd.android.package-archive"),
    ("dist/MusicLinker-v3.0.8-windows-x64.zip", "application/zip"),
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
