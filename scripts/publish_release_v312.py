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
tag_name = "v3.1.2"
release_title = "MusicLinker v3.1.2 — Candidate Video Thumbnails & Album Badges, Studio Volume Equalizer & Auto-Polling Last.fm Auth"
release_body = """### MusicLinker v3.1.2 Release Notes

#### 1. Audio Candidate Cards: Thumbnails & Album Metadata Badges
- **Live Video Thumbnails**: Candidate cards in the "Change Audio Version" modal now display high-resolution video/album art thumbnails with fallback icons.
- **Album Name Badges**: Displays official album titles (`💽 Album Name`) on candidate cards, allowing instant visual differentiation between single releases, live bootlegs, and official studio albums.
- **Candidate Presentation**: Modernized tactile candidate layout with duration delta pill indicators (`+3s`, `Exact duration`).

#### 2. Robust Audio Substitution & Windows Lock Resilience
- **Real-Time Visual Feedback**: The modal displays a dedicated progress banner (`Downloading audio & replacing file on disk...`) and disables candidate buttons to prevent accidental double-clicks.
- **Player State Decoupling**: If the track currently playing is being swapped, MusicLinker safely pauses and unloads the media stream before disk replacement, preventing `[WinError 32]` file access conflicts on Windows.
- **Cover Art & Tag Preservation**: Embedded album art and metadata are extracted before overwriting and re-embedded into the new audio file without losing original album artwork.
- **Lock-Resilient Fallback**: Automatic retry loop and versioned path fallback if Windows holds a lingering read handle on the file, ensuring substitution never stalls.

#### 3. Studio Volume Equalizer & Anti-Clip Limiter (DSP)
- **Master Preamp Volume Equalizer Slider**: Independent gain stage from -12 dB to +12 dB with real-time dB readout and linear multiplier display (`+3 dB • 1.4x Gain`).
- **Quick Volume Boost Buttons**: Instant presets for `-6 dB`, `0 dB (Flat)`, `+3 dB`, `+6 dB`, and `+9 dB`.
- **Dynamics Compressor / Studio Limiter**: Integrated Web Audio `DynamicsCompressorNode` preventing audio clipping and distortion when boosting bass or preamp levels.
- **Persistent DSP State**: Preamp volume and limiter settings persist in `localStorage` alongside the 5-band equalizer gains.

#### 4. Automated 1-Click Last.fm Web Authorization
- **Auto-Polling Authorization**: MusicLinker continuously checks authorization status every 2 seconds after launching the browser tab. Once the user clicks "Yes, allow access" on Last.fm, the app automatically detects approval, completes linking, and closes the modal without manual interaction.
- **Instant Focus Polling**: Tabbing back to the application immediately triggers an authorization check.
- **Live Approval Status**: Modal shows an animated spinner and polling status indicator with an optional manual "Check Now" button.
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
    ("MusicLinker-v3.1.2.apk", "application/vnd.android.package-archive"),
    ("MusicLinker-v3.1.2-windows-x64.zip", "application/zip"),
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

print(f"\nAll v3.1.2 release assets successfully uploaded to {existing_release['html_url']}")
