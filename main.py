import os
import sys
import argparse
from datetime import datetime
from typing import List, Dict, Any, Optional

from config import load_config, save_config, get_api_key
from lastfm import LastFMClient, LastFMError
from hasher import TrackHashManager
from youtube import YouTubeSearcher
from downloader import TrackDownloader

BANNER = r"""
===================================================================
     Last.fm Music Linker & YouTube Extractor
===================================================================
"""

def parse_date_to_ts(date_str: str, end_of_day: bool = False) -> int:
    dt = datetime.strptime(date_str.strip(), "%Y-%m-%d")
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    return int(dt.timestamp())

def run_extraction(
    username: str,
    api_key: str,
    period: str = "12month",
    year: Optional[int] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    min_plays: int = 10,
    output_links: str = "youtube_links.txt",
    output_detailed: str = "songs_detailed.txt",
    hash_file: str = "seen_hashes.json",
    import_list: Optional[str] = None,
    do_download: bool = False,
    download_dir: str = "downloads"
) -> None:
    print(BANNER)
    print(f"Target Last.fm User : {username}")
    print(f"Min Playcount Filter: > {min_plays} plays")
    
    # 1. Initialize Duplicate / Hash Manager
    hasher = TrackHashManager(storage_file=hash_file)
    print(f"Loaded {len(hasher.get_all_hashes())} previously seen songs from hash store ({hash_file}).")
    
    if import_list and os.path.exists(import_list):
        imported = hasher.import_external_file(import_list)
        print(f"Imported {imported} tracks from external list: {import_list}")

    # 2. Query Last.fm
    client = LastFMClient(api_key=api_key)
    tracks: List[Dict[str, Any]] = []

    try:
        if year:
            print(f"Fetching listening history for calendar year: {year}...")
            start_ts = int(datetime(year, 1, 1, 0, 0, 0).timestamp())
            end_ts = int(datetime(year, 12, 31, 23, 59, 59).timestamp())
            tracks = client.get_tracks_custom_range(
                username=username,
                from_ts=start_ts,
                to_ts=end_ts,
                min_plays=min_plays,
                progress_cb=lambda cur, tot, msg: print(f"  [{cur}/{tot}] {msg}")
            )
        elif from_date and to_date:
            print(f"Fetching listening history from {from_date} to {to_date}...")
            start_ts = parse_date_to_ts(from_date, end_of_day=False)
            end_ts = parse_date_to_ts(to_date, end_of_day=True)
            tracks = client.get_tracks_custom_range(
                username=username,
                from_ts=start_ts,
                to_ts=end_ts,
                min_plays=min_plays,
                progress_cb=lambda cur, tot, msg: print(f"  [{cur}/{tot}] {msg}")
            )
        else:
            print(f"Fetching top tracks for period: '{period}'...")
            tracks = client.get_top_tracks(
                username=username,
                period=period,
                min_plays=min_plays,
                progress_cb=lambda cur, tot, msg: print(f"  [{cur}/{tot}] {msg}")
            )
    except LastFMError as e:
        print(f"\n[Error] {e}")
        return

    print(f"\nFound {len(tracks)} tracks with > {min_plays} plays.")
    if not tracks:
        print("No tracks met the criteria. Check username or try lowering min_plays.")
        return

    # 3. Filter using Hash Check
    unseen_tracks: List[Dict[str, Any]] = []
    skipped_count = 0

    for t in tracks:
        if hasher.is_seen(t["artist"], t["title"]):
            skipped_count += 1
        else:
            unseen_tracks.append(t)

    print(f"Duplicate / Hash check: {skipped_count} skipped (already seen), {len(unseen_tracks)} new to process.")

    if not unseen_tracks:
        print("All tracks have already been processed in previous runs. Nothing new to do!")
        return

    # 4. Search YouTube Links
    yt_searcher = YouTubeSearcher()
    downloader = TrackDownloader(base_output_dir=download_dir) if do_download else None

    processed_results: List[Dict[str, Any]] = []
    print("\nSearching YouTube for matching songs...")

    for idx, t in enumerate(unseen_tracks, start=1):
        artist = t["artist"]
        title = t["title"]
        plays = t["plays"]
        print(f"[{idx}/{len(unseen_tracks)}] {artist} - {title} ({plays} plays)...", end=" ", flush=True)

        yt_res = yt_searcher.search_track(artist, title)
        if yt_res and yt_res.get("url"):
            yt_url = yt_res["url"]
            print(f"Found -> {yt_url}")
            t["youtube_url"] = yt_url
            processed_results.append(t)

            # Mark in Hash Store immediately
            hasher.mark_seen(artist, title, youtube_url=yt_url, metadata={"plays": plays})

            # Download if requested
            if do_download and downloader:
                album_info = client.get_track_info(artist, title)
                album_name = album_info.get("album") or t.get("album", "")
                cover_url = album_info.get("cover_url") or t.get("image_url", "")
                
                downloader.download_and_tag(
                    youtube_url=yt_url,
                    artist=artist,
                    title=title,
                    album=album_name,
                    cover_url=cover_url,
                    plays=plays,
                    progress_cb=lambda msg: print(f"    -> {msg}")
                )
        else:
            print("Not found on YouTube.")

    # Save hash store updates
    hasher.save()

    # 5. Output Results
    # File 1: Just YouTube links (as requested)
    with open(output_links, "a", encoding="utf-8") as f:
        for item in processed_results:
            f.write(item["youtube_url"] + "\n")

    # File 2: Detailed reference list
    with open(output_detailed, "a", encoding="utf-8") as f:
        for item in processed_results:
            f.write(f"{item['artist']} - {item['title']} | Plays: {item['plays']} | {item['youtube_url']}\n")

    print("\n" + "=" * 65)
    print(" EXTRACTION COMPLETE")
    print("=" * 65)
    print(f"Successfully processed : {len(processed_results)} new songs")
    print(f"Links-only text file   : {os.path.abspath(output_links)}")
    print(f"Detailed list file     : {os.path.abspath(output_detailed)}")
    print(f"Hash store updated     : {os.path.abspath(hash_file)}")
    if do_download:
        print(f"Categorized MP3s saved : {os.path.abspath(download_dir)}")
    print("=" * 65)

def interactive_wizard() -> None:
    print(BANNER)
    config = load_config()
    api_key = get_api_key(interactive=True)

    default_user = config.get("default_username", "")
    prompt_user = f"Enter Last.fm username [{default_user}]: " if default_user else "Enter Last.fm username: "
    username = input(prompt_user).strip()
    if not username and default_user:
        username = default_user
    while not username:
        username = input("Last.fm username cannot be empty: ").strip()

    config["default_username"] = username
    save_config(config)

    print("\nChoose Time Period:")
    print("  1) Last 12 Months (1 Year) [Default]")
    print("  2) Last 6 Months")
    print("  3) Last 3 Months")
    print("  4) Last 30 Days")
    print("  5) Last 7 Days")
    print("  6) All Time")
    print("  7) Specific Calendar Year (e.g. 2023, 2024, 2025)")
    print("  8) Custom Date Range (YYYY-MM-DD to YYYY-MM-DD)")
    choice = input("Select option (1-8) [1]: ").strip() or "1"

    period = "12month"
    year = None
    from_date = None
    to_date = None

    if choice == "1":
        period = "12month"
    elif choice == "2":
        period = "6month"
    elif choice == "3":
        period = "3month"
    elif choice == "4":
        period = "1month"
    elif choice == "5":
        period = "7day"
    elif choice == "6":
        period = "overall"
    elif choice == "7":
        yr_str = input("Enter 4-digit Year (e.g. 2023): ").strip()
        try:
            year = int(yr_str)
        except ValueError:
            print("Invalid year. Falling back to 12month.")
            period = "12month"
    elif choice == "8":
        from_date = input("Enter start date (YYYY-MM-DD): ").strip()
        to_date = input("Enter end date   (YYYY-MM-DD): ").strip()

    min_plays_str = input("Minimum plays threshold (e.g. 10 for > 10 plays) [10]: ").strip()
    try:
        min_plays = int(min_plays_str) if min_plays_str else 10
    except ValueError:
        min_plays = 10

    # Optional import of existing text file
    import_file = None
    do_import = input("\nDo you want to check against an existing song list to avoid repeats? (y/N): ").strip().lower()
    if do_import in ("y", "yes"):
        filepath = input("Enter file path to existing list: ").strip()
        if os.path.exists(filepath):
            import_file = filepath
        else:
            print(f"File '{filepath}' not found, continuing with standard hash store.")

    # Optional download
    do_download_input = input("\nDo you also want to download and tag them as categorized MP3s? (y/N): ").strip().lower()
    do_download = do_download_input in ("y", "yes")

    run_extraction(
        username=username,
        api_key=api_key,
        period=period,
        year=year,
        from_date=from_date,
        to_date=to_date,
        min_plays=min_plays,
        import_list=import_file,
        do_download=do_download
    )

def main():
    parser = argparse.ArgumentParser(description="Extract Last.fm songs with > N plays to YouTube links with hash deduplication.")
    parser.add_argument("-u", "--username", help="Last.fm username")
    parser.add_argument("-k", "--api-key", help="Last.fm API key")
    parser.add_argument("-p", "--period", default="12month", choices=["7day", "1month", "3month", "6month", "12month", "overall"], help="Preset period (default: 12month)")
    parser.add_argument("-y", "--year", type=int, help="Specific calendar year (e.g. 2023)")
    parser.add_argument("--from-date", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--to-date", help="End date (YYYY-MM-DD)")
    parser.add_argument("-m", "--min-plays", type=int, default=10, help="Minimum plays threshold (default: 10)")
    parser.add_argument("-o", "--output-links", default="youtube_links.txt", help="Output file for YouTube links only")
    parser.add_argument("-d", "--output-detailed", default="songs_detailed.txt", help="Output file for detailed track list")
    parser.add_argument("--hash-file", default="seen_hashes.json", help="Persistent hash storage file")
    parser.add_argument("--import-list", help="Import an existing text list to avoid duplicates")
    parser.add_argument("--download", action="store_true", help="Download MP3s with ID3 tags and album cover art")
    parser.add_argument("--download-dir", default="downloads", help="Directory to save categorized MP3s")

    args = parser.parse_args()

    # If no username specified, open interactive wizard
    if not args.username:
        interactive_wizard()
        return

    config = load_config()
    api_key = args.api_key or config.get("lastfm_api_key") or get_api_key(interactive=True)

    run_extraction(
        username=args.username,
        api_key=api_key,
        period=args.period,
        year=args.year,
        from_date=args.from_date,
        to_date=args.to_date,
        min_plays=args.min_plays,
        output_links=args.output_links,
        output_detailed=args.output_detailed,
        hash_file=args.hash_file,
        import_list=args.import_list,
        do_download=args.download,
        download_dir=args.download_dir
    )

if __name__ == "__main__":
    main()
