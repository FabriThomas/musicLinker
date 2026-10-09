import os
import streamlit as st
import pandas as pd
from datetime import datetime, date

from config import load_config, save_config
from lastfm import LastFMClient, LastFMError
from hasher import TrackHashManager, compute_track_hash
from youtube import YouTubeSearcher
from downloader import TrackDownloader

st.set_page_config(
    page_title="Last.fm to YouTube Linker & Downloader",
    page_icon="🎵",
    layout="wide"
)

# Custom header styling
st.markdown("""
<style>
.main-header {
    font-size: 2.2rem;
    font-weight: 700;
    color: #e50914;
    margin-bottom: 0.2rem;
}
.sub-header {
    font-size: 1.05rem;
    color: #888;
    margin-bottom: 1.5rem;
}
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="main-header">🎵 Last.fm Music Linker & Extractor</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">Extract songs with &gt; 10 plays, eliminate duplicates via hash checking, generate YouTube links, and download categorized MP3s with ID3 tags.</div>', unsafe_allow_html=True)

config = load_config()

# Sidebar: Configuration & Last.fm API Key
with st.sidebar:
    st.header("🔑 Last.fm Credentials")
    saved_key = config.get("lastfm_api_key", "")
    api_key_input = st.text_input("Last.fm API Key", value=saved_key, type="password", help="Required to access Last.fm data without blocks.")

    with st.expander("ℹ️ How to get a free API key (30 sec)"):
        st.markdown("""
        1. Open [Last.fm API Account Creation](https://www.last.fm/api/account/create) (login if needed).
        2. Set **Application Name** to `MusicLinker`.
        3. Leave callback empty and click **Submit**.
        4. Copy the **API Key** shown and paste it above!
        """)

    saved_user = config.get("default_username", "")
    username_input = st.text_input("Last.fm Username", value=saved_user)

    if st.button("💾 Save Credentials"):
        config["lastfm_api_key"] = api_key_input.strip()
        config["default_username"] = username_input.strip()
        save_config(config)
        st.success("Credentials saved to config.json!")

    st.markdown("---")
    st.header("⚙️ Duplicate & Hash Store")
    hasher = TrackHashManager(storage_file="seen_hashes.json")
    st.info(f"Loaded **{len(hasher.get_all_hashes())}** previously seen tracks in `seen_hashes.json`.")

    uploaded_list = st.file_uploader("Upload existing text list to check duplicates against", type=["txt", "csv", "log"])
    if uploaded_list is not None:
        content = uploaded_list.getvalue().decode("utf-8", errors="ignore")
        temp_path = "temp_uploaded_list.txt"
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(content)
        imported_count = hasher.import_external_file(temp_path)
        if os.path.exists(temp_path):
            os.remove(temp_path)
        st.success(f"Imported {imported_count} tracks into duplicate protection!")

# Main parameters
col1, col2, col3 = st.columns([1.5, 1.2, 1.2])

with col1:
    timeframe_option = st.selectbox(
        "Timeframe / Period",
        [
            "Last 12 Months (1 Year)",
            "Last 6 Months",
            "Last 3 Months",
            "Last 30 Days",
            "Last 7 Days",
            "All Time",
            "Specific Calendar Year",
            "Custom Date Range"
        ],
        index=0
    )

with col2:
    min_plays = st.number_input(
        "Minimum Plays Filter (> X plays)",
        min_value=1,
        max_value=10000,
        value=int(config.get("min_plays", 10)),
        step=1,
        help="Only tracks with strictly more than this number of plays will be included."
    )

with col3:
    do_download_mp3 = st.checkbox(
        "Download MP3s with ID3 Tags",
        value=False,
        help="Downloads audio, fetches album artwork and tags ID3 (Artist, Title, Album) for your music library."
    )

# Secondary controls for custom date ranges
selected_year = None
from_ts = None
to_ts = None

if timeframe_option == "Specific Calendar Year":
    selected_year = st.number_input("Calendar Year", min_value=2002, max_value=2030, value=datetime.now().year - 1)
elif timeframe_option == "Custom Date Range":
    d_col1, d_col2 = st.columns(2)
    with d_col1:
        d_start = st.date_input("Start Date", value=date(datetime.now().year, 1, 1))
    with d_col2:
        d_end = st.date_input("End Date", value=date.today())
    from_ts = int(datetime.combine(d_start, datetime.min.time()).timestamp())
    to_ts = int(datetime.combine(d_end, datetime.max.time()).timestamp())

# Map preset period
period_map = {
    "Last 12 Months (1 Year)": "12month",
    "Last 6 Months": "6month",
    "Last 3 Months": "3month",
    "Last 30 Days": "1month",
    "Last 7 Days": "7day",
    "All Time": "overall"
}

st.markdown("---")

run_btn = st.button("🚀 Extract Songs & Generate YouTube Links", type="primary", use_container_width=True)

if run_btn:
    active_api_key = api_key_input.strip() or config.get("lastfm_api_key", "").strip()
    active_username = username_input.strip() or config.get("default_username", "").strip()

    if not active_api_key:
        st.error("Please enter your Last.fm API Key in the sidebar.")
        st.stop()

    if not active_username:
        st.error("Please enter your Last.fm username.")
        st.stop()

    client = LastFMClient(api_key=active_api_key)
    progress_bar = st.progress(0.0)
    status_text = st.empty()

    status_text.text("Connecting to Last.fm...")
    try:
        if selected_year:
            s_ts = int(datetime(int(selected_year), 1, 1, 0, 0, 0).timestamp())
            e_ts = int(datetime(int(selected_year), 12, 31, 23, 59, 59).timestamp())
            raw_tracks = client.get_tracks_custom_range(
                username=active_username,
                from_ts=s_ts,
                to_ts=e_ts,
                min_plays=min_plays,
                progress_cb=lambda cur, tot, msg: status_text.text(f"Last.fm: {msg}")
            )
        elif from_ts and to_ts:
            raw_tracks = client.get_tracks_custom_range(
                username=active_username,
                from_ts=from_ts,
                to_ts=to_ts,
                min_plays=min_plays,
                progress_cb=lambda cur, tot, msg: status_text.text(f"Last.fm: {msg}")
            )
        else:
            p_code = period_map.get(timeframe_option, "12month")
            raw_tracks = client.get_top_tracks(
                username=active_username,
                period=p_code,
                min_plays=min_plays,
                progress_cb=lambda cur, tot, msg: status_text.text(f"Last.fm: {msg}")
            )
    except LastFMError as e:
        st.error(f"Last.fm Error: {e}")
        st.stop()

    if not raw_tracks:
        st.warning(f"No tracks found for user '{active_username}' with > {min_plays} plays in this period.")
        st.stop()

    st.success(f"Found **{len(raw_tracks)}** candidate tracks with > {min_plays} plays!")

    # Duplicate / Hash check
    unseen_tracks = []
    skipped_count = 0
    for t in raw_tracks:
        if hasher.is_seen(t["artist"], t["title"]):
            skipped_count += 1
        else:
            unseen_tracks.append(t)

    col_stat1, col_stat2 = st.columns(2)
    col_stat1.metric("New Tracks (Not in Hash List)", len(unseen_tracks))
    col_stat2.metric("Duplicates Skipped", skipped_count)

    if not unseen_tracks:
        st.info("All tracks have already been processed in previous runs. No new YouTube searches needed!")
        st.stop()

    # YouTube Resolution & Optional Download
    yt_searcher = YouTubeSearcher()
    downloader = TrackDownloader(base_output_dir="downloads") if do_download_mp3 else None

    processed_list = []
    total_unseen = len(unseen_tracks)

    for i, t in enumerate(unseen_tracks):
        frac = (i + 1) / total_unseen
        progress_bar.progress(frac)
        status_text.text(f"Processing ({i+1}/{total_unseen}): {t['artist']} - {t['title']}...")

        yt_res = yt_searcher.search_track(t["artist"], t["title"])
        yt_url = yt_res.get("url", "") if yt_res else ""

        if yt_url:
            t["youtube_url"] = yt_url
            hasher.mark_seen(t["artist"], t["title"], youtube_url=yt_url, metadata={"plays": t["plays"]})
            processed_list.append(t)

            if do_download_mp3 and downloader:
                album_meta = client.get_track_info(t["artist"], t["title"])
                album_name = album_meta.get("album") or t.get("album", "")
                cover_url = album_meta.get("cover_url") or t.get("image_url", "")
                
                status_text.text(f"Downloading MP3 & Tagging: {t['artist']} - {t['title']}...")
                downloader.download_and_tag(
                    youtube_url=yt_url,
                    artist=t["artist"],
                    title=t["title"],
                    album=album_name,
                    cover_url=cover_url,
                    plays=t["plays"]
                )

    hasher.save()
    progress_bar.progress(1.0)
    status_text.text("Extraction completed successfully!")

    # Display results
    st.markdown("### 📋 Results")

    table_data = []
    for item in processed_list:
        table_data.append({
            "Artist": item["artist"],
            "Title": item["title"],
            "Plays": item["plays"],
            "YouTube Link": item.get("youtube_url", ""),
            "Hash": compute_track_hash(item["artist"], item["title"])
        })

    df = pd.DataFrame(table_data)
    st.dataframe(df, use_container_width=True)

    # Export Buttons
    links_only_text = "\n".join([item.get("youtube_url", "") for item in processed_list if item.get("youtube_url")])
    detailed_text = "\n".join([f"{item['artist']} - {item['title']} | Plays: {item['plays']} | {item.get('youtube_url', '')}" for item in processed_list])

    exp_col1, exp_col2 = st.columns(2)
    with exp_col1:
        st.download_button(
            label="📥 Download YouTube Links Only (.txt)",
            data=links_only_text,
            file_name="youtube_links.txt",
            mime="text/plain",
            use_container_width=True
        )

    with exp_col2:
        st.download_button(
            label="📥 Download Detailed Track List (.txt)",
            data=detailed_text,
            file_name="songs_detailed.txt",
            mime="text/plain",
            use_container_width=True
        )

    if do_download_mp3:
        st.success(f"🎵 MP3s downloaded and tagged with ID3 & cover art in the `downloads/` folder!")
