"""Interactive CLI Wizard and scriptable headless runner for Last.fm extraction and Full Album downloading."""

from __future__ import annotations

import argparse
import sys
import time

from musiclinker.domain.models import AlbumDownloadRequest, ExtractionJobRequest, JobStatus
from musiclinker.infrastructure.db import db_repo
from musiclinker.services.job_service import job_service


def run_interactive_wizard() -> ExtractionJobRequest:
    cfg = db_repo.get_settings()
    print("=== MusicLinker v3.0 Interactive CLI Wizard ===")
    default_user = cfg.get("lastfm_username") or ""
    username = input(f"Last.fm Username [{default_user}]: ").strip() or default_user
    if not username:
        print("Error: Last.fm username is required.")
        sys.exit(1)

    print("Periods: 7day | 1month | 3month | 6month | 12month | overall | custom_year")
    period = input("Select Period [12month]: ").strip() or "12month"
    year = None
    if period == "custom_year":
        year_str = input("Enter Calendar Year (e.g. 2025): ").strip()
        year = int(year_str)

    min_plays_str = input(f"Minimum Scrobble Plays [{cfg.get('default_min_plays', 10)}]: ").strip()
    min_plays = int(min_plays_str) if min_plays_str else int(cfg.get("default_min_plays", 10))

    cap_str = input(f"Max Tracks to Download [{cfg.get('default_download_cap', 25)}]: ").strip()
    download_cap = int(cap_str) if cap_str else int(cfg.get("default_download_cap", 25))

    bitrate_str = input(f"MP3 Bitrate (192 or 320) [{cfg.get('default_bitrate_kbps', 320)}]: ").strip()
    bitrate = int(bitrate_str) if bitrate_str in ("192", "320") else 320

    llm_choice = input("Use LLM Smart Filter to deduplicate & fix albums before download? (Y/n): ").strip().lower()
    use_llm = llm_choice not in ("n", "no")

    dl_choice = input("Download MP3s now? (Y/n): ").strip().lower()
    download_enabled = dl_choice not in ("n", "no")

    return ExtractionJobRequest(
        username=username,
        period=period,
        year=year,
        min_plays=min_plays,
        download_cap=download_cap,
        bitrate_kbps=bitrate,
        download_enabled=download_enabled,
        use_llm_filter=use_llm,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="MusicLinker Headless CLI & Album Downloader")
    parser.add_argument("--username", type=str, help="Last.fm username")
    parser.add_argument("--period", type=str, default="12month", help="Last.fm period or 'custom_year'")
    parser.add_argument("--year", type=int, default=None, help="Calendar year when --period custom_year")
    parser.add_argument("--min-plays", type=int, default=10, help="Minimum play count filter (default: 10)")
    parser.add_argument("--cap", type=int, default=25, help="Maximum tracks to download (default: 25)")
    parser.add_argument("--bitrate", type=int, default=320, choices=[192, 320], help="Target MP3 bitrate")
    parser.add_argument("--llm-filter", action="store_true", help="Enable LLM deduplication & album fixing")
    parser.add_argument("--download", action="store_true", help="Enable MP3 downloading in non-interactive mode")
    parser.add_argument("--artist", type=str, help="Artist name for full album download mode")
    parser.add_argument("--album", type=str, help="Album title or playlist URL for full album download mode")
    args = parser.parse_args()

    if args.album:
        job = job_service.start_album_download_job(
            AlbumDownloadRequest(
                artist=args.artist or "Unknown Artist",
                album=args.album,
                bitrate_kbps=args.bitrate,
            )
        )
    elif not args.username:
        req = run_interactive_wizard()
        job = job_service.start_lastfm_extraction_job(req)
    else:
        req = ExtractionJobRequest(
            username=args.username,
            period=args.period,
            year=args.year,
            min_plays=args.min_plays,
            download_cap=args.cap,
            bitrate_kbps=args.bitrate,
            download_enabled=args.download,
            use_llm_filter=args.llm_filter,
        )
        job = job_service.start_lastfm_extraction_job(req)

    seen_logs = 0
    while job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
        current_logs = job.logs
        while seen_logs < len(current_logs):
            print(current_logs[seen_logs])
            seen_logs += 1
        time.sleep(0.5)

    for line in job.logs[seen_logs:]:
        print(line)

    if job.status == JobStatus.COMPLETED:
        print(f"\nSuccess! Downloaded {job.downloaded_count} track(s).")
    else:
        print(f"\nJob finished with status: {job.status.value}")
        sys.exit(1)


if __name__ == "__main__":
    main()