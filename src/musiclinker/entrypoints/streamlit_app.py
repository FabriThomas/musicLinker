"""Streamlit prototyping and analytics dashboard backed by the unified MusicLinker service layer."""

from __future__ import annotations

import streamlit as st

from musiclinker.infrastructure.db import db_repo
from musiclinker.infrastructure.lastfm_client import LastFMClient


def render_dashboard() -> None:
    st.set_page_config(page_title="MusicLinker Analytics", page_icon="🎵", layout="wide")
    st.title("🎵 MusicLinker Library & Last.fm Explorer")

    cfg = db_repo.get_settings()
    tracks = [t.to_dict() for t in db_repo.list_tracks()]

    col1, col2, col3 = st.columns(3)
    col1.metric("Indexed Library Tracks", len(tracks))
    col2.metric("Distinct Albums", len(db_repo.list_albums_summary()))
    col3.metric("Pending Offline Scrobbles", len(db_repo.get_pending_scrobbles(limit=500)))

    st.subheader("Indexed Local Library")
    if tracks:
        st.dataframe(
            tracks,
            column_order=[
                "id",
                "track_number",
                "artist_display",
                "title",
                "album",
                "lyric_sync_mode",
                "original_year",
                "release_year",
                "lastfm_playcount",
                "local_playcount",
                "bitrate_kbps",
            ],
            use_container_width=True,
        )
    else:
        st.info("No tracks indexed yet. Run an extraction job or place MP3s inside downloads/.")

    st.subheader("Live Last.fm Filter Preview")
    with st.form("preview_form"):
        username = st.text_input("Last.fm Username", value=cfg.get("lastfm_username", ""))
        period = st.selectbox("Period", ["7day", "1month", "3month", "6month", "12month", "overall"], index=4)
        min_plays = st.number_input("Minimum Plays", min_value=1, value=10)
        submitted = st.form_submit_button("Preview Qualifying Tracks")

    if submitted and username:
        client = LastFMClient(api_key=str(cfg.get("lastfm_api_key", "")))
        candidates = client.fetch_top_tracks_by_period(username=username, period=period, min_plays=int(min_plays))
        rows = [
            {
                "Artist": c.artist,
                "Title": c.title,
                "Plays": c.playcount,
                "Hash": c.track_hash,
                "Already Seen": db_repo.is_hash_seen(c.track_hash),
            }
            for c in candidates
        ]
        st.write(f"Found **{len(rows)}** tracks with >= {min_plays} plays:")
        st.dataframe(rows, use_container_width=True)


if __name__ == "__main__":
    render_dashboard()