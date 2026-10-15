"""LLM Smart Curator & Heuristic Deduplicator for detecting repeated tracks and canonicalizing albums."""

from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from musiclinker.core.logging_config import logger
from musiclinker.domain.models import (
    CandidateTrack,
    LLMCurationAction,
    TrackMetadataUpdatePayload,
)
from musiclinker.infrastructure.db import DatabaseRepository, db_repo
from musiclinker.infrastructure.hasher import (
    clean_album_name,
    clean_track_title,
    compute_track_hash,
    normalize_text,
)
from musiclinker.infrastructure.llm_client import LLMClient
from musiclinker.services.library_service import LibraryService, library_service


class CurationService:
    """Provides LLM API/Proxy and fuzzy-matching deduplication and track/album canonicalization."""

    def __init__(
        self,
        repo: DatabaseRepository = db_repo,
        lib_service: LibraryService = library_service,
    ) -> None:
        self.repo = repo
        self.lib_service = lib_service

    def _build_llm(self) -> LLMClient:
        cfg = self.repo.get_settings()
        return LLMClient(
            base_url=str(cfg.get("llm_base_url", "https://api.openai.com/v1")),
            api_key=str(cfg.get("llm_api_key", "")),
            model=str(cfg.get("llm_model", "gpt-4o-mini")),
        )

    def filter_candidates_before_download(
        self,
        candidates: list[CandidateTrack],
        log_cb: Any = None,
    ) -> list[CandidateTrack]:
        if not candidates:
            return []

        llm = self._build_llm()
        if llm.is_configured():
            try:
                if log_cb:
                    log_cb(f"Running LLM Smart Filter via {llm.base_url} (model={llm.model}) on {len(candidates)} candidate(s)...")
                payload = [
                    {
                        "id": idx,
                        "title": c.title,
                        "artist": c.artist,
                        "album": c.album,
                        "playcount": c.playcount,
                    }
                    for idx, c in enumerate(candidates)
                ]
                actions = llm.curate_tracks_json(payload[:80])
                action_map = {int(a["track_id"]): a for a in actions if "track_id" in a}
                curated: list[CandidateTrack] = []
                removed_dups = 0
                for idx, cand in enumerate(candidates):
                    act = action_map.get(idx)
                    if act and act.get("action_type") == "duplicate_remove":
                        removed_dups += 1
                        continue
                    if act and act.get("action_type") == "rename":
                        cand.title = str(act.get("proposed_title") or cand.title).strip()
                        cand.artist = str(act.get("proposed_artist") or cand.artist).strip()
                        cand.album = str(act.get("proposed_album") or cand.album).strip()
                    else:
                        cand.title = clean_track_title(cand.title)
                        cand.album = clean_album_name(cand.album, cand.title)
                    cand.track_hash = compute_track_hash(cand.artist, cand.title)
                    curated.append(cand)
                if log_cb:
                    log_cb(f"LLM Filter complete: removed {removed_dups} duplicate version(s), {len(curated)} unique track(s) retained.")
                return curated
            except Exception as exc:
                logger.warning("LLM proxy call failed (%s); falling back to local fuzzy deduplicator", exc)
                if log_cb:
                    log_cb(f"LLM proxy unreachable ({exc}); using built-in fuzzy deduplicator.")

        deduped: list[CandidateTrack] = []
        for cand in candidates:
            cand.title = clean_track_title(cand.title)
            cand.album = self.repo.resolve_album_alias(cand.artist, cand.album, cand.title)
            cand.track_hash = compute_track_hash(cand.artist, cand.title)

            matched_existing = None
            for kept in deduped:
                if normalize_text(kept.artist) == normalize_text(cand.artist):
                    ratio = SequenceMatcher(None, normalize_text(kept.title), normalize_text(cand.title)).ratio()
                    if ratio >= 0.88:
                        matched_existing = kept
                        break
            if matched_existing:
                matched_existing.playcount += cand.playcount
            else:
                deduped.append(cand)
        return deduped

    def analyze_library(self) -> dict[str, Any]:
        tracks = self.repo.list_tracks()
        if not tracks:
            return {"used_llm": False, "actions": []}

        track_map = {t.id: t for t in tracks}
        llm = self._build_llm()
        used_llm = False
        actions: list[LLMCurationAction] = []

        if llm.is_configured():
            try:
                payload = [
                    {
                        "id": t.id,
                        "title": t.title,
                        "artist": t.artist_display,
                        "album": t.album,
                        "playcount": t.lastfm_playcount + t.local_playcount,
                    }
                    for t in tracks[:100]
                ]
                raw_actions = llm.curate_tracks_json(payload)
                used_llm = True
                for item in raw_actions:
                    tid = int(item.get("track_id", 0))
                    orig = track_map.get(tid)
                    if not orig:
                        continue
                    act_type = str(item.get("action_type", "rename"))
                    if act_type not in ("rename", "duplicate_remove"):
                        continue
                    actions.append(
                        LLMCurationAction(
                            track_id=tid,
                            action_type=act_type,
                            current_title=orig.title,
                            proposed_title=str(item.get("proposed_title") or orig.title).strip(),
                            current_artist=orig.artist_display,
                            proposed_artist=str(item.get("proposed_artist") or orig.artist_display).strip(),
                            current_album=orig.album,
                            proposed_album=str(item.get("proposed_album") or orig.album).strip(),
                            duplicate_of_track_id=item.get("duplicate_of_track_id"),
                            reason=str(item.get("reason") or "LLM metadata normalization"),
                        )
                    )
            except Exception as exc:
                logger.warning("Library LLM audit fallback triggered: %s", exc)

        if not actions:
            seen_by_artist: dict[str, list[Any]] = {}
            for t in tracks:
                cleaned_title = clean_track_title(t.title)
                cleaned_album = self.repo.resolve_album_alias(t.album_artist, t.album, cleaned_title)
                art_key = normalize_text(t.album_artist or t.artist_display)

                dup_target = None
                for prev in seen_by_artist.get(art_key, []):
                    sim = SequenceMatcher(None, normalize_text(prev.title), normalize_text(cleaned_title)).ratio()
                    if sim >= 0.88:
                        dup_target = prev
                        break

                if dup_target is not None:
                    actions.append(
                        LLMCurationAction(
                            track_id=t.id,
                            action_type="duplicate_remove",
                            current_title=t.title,
                            proposed_title=dup_target.title,
                            current_artist=t.artist_display,
                            proposed_artist=dup_target.artist_display,
                            current_album=t.album,
                            proposed_album=dup_target.album,
                            duplicate_of_track_id=dup_target.id,
                            reason=f"Duplicate version of '{dup_target.title}' (ID #{dup_target.id})",
                        )
                    )
                    continue

                seen_by_artist.setdefault(art_key, []).append(t)
                if cleaned_title != t.title or cleaned_album != t.album:
                    actions.append(
                        LLMCurationAction(
                            track_id=t.id,
                            action_type="rename",
                            current_title=t.title,
                            proposed_title=cleaned_title,
                            current_artist=t.artist_display,
                            proposed_artist=t.artist_display,
                            current_album=t.album,
                            proposed_album=cleaned_album,
                            reason="Auto-cleaned edition/remaster noise",
                        )
                    )

        return {
            "used_llm": used_llm,
            "llm_endpoint": llm.base_url if used_llm else "local-heuristic",
            "actions": [a.model_dump() for a in actions],
        }

    def run_agent_command(
        self,
        instruction: str,
        history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Execute an autonomous curation instruction via LLM reasoning with library tool actions and clean debugging."""
        tracks = self.repo.list_tracks()
        artists_summary = self.repo.list_artists_summary()
        albums_summary = self.repo.list_albums_summary()
        total_tracks = len(tracks)

        # 1. Extract potential artist/album keywords from instruction
        lower_ins = instruction.lower()
        matched_keywords: list[str] = []
        focused_tracks: list[Any] = []
        seen_track_ids: set[int] = set()

        for a in artists_summary:
            art_name = str(a.get("artist") or "")
            if art_name and len(art_name) >= 3 and art_name.lower() in lower_ins:
                matched_keywords.append(art_name)
                matching = self.repo.list_tracks(artist=art_name)
                for t in matching:
                    if t.id not in seen_track_ids:
                        seen_track_ids.add(t.id)
                        focused_tracks.append(t)

        for alb in albums_summary:
            alb_name = str(alb.get("album") or "")
            if alb_name and len(alb_name) >= 3 and alb_name.lower() in lower_ins:
                matched_keywords.append(alb_name)
                matching = self.repo.list_tracks(album=alb_name)
                for t in matching:
                    if t.id not in seen_track_ids:
                        seen_track_ids.add(t.id)
                        focused_tracks.append(t)

        # 2. Add tracks with potential noise or candidate duplicates to prioritize curation
        noisy_tracks: list[Any] = []
        for t in tracks:
            if t.id in seen_track_ids:
                continue
            t_lower = t.title.lower()
            if any(term in t_lower for term in ("remaster", "official", "deluxe", "live", "edit", "version", "feat.", "ft.")):
                noisy_tracks.append(t)
                seen_track_ids.add(t.id)
            elif "- single" in t.album.lower():
                noisy_tracks.append(t)
                seen_track_ids.add(t.id)
            if len(noisy_tracks) >= 50:
                break

        # 3. Fill remaining sample up to 150 tracks
        remaining_sample: list[Any] = []
        for t in tracks:
            if t.id not in seen_track_ids:
                remaining_sample.append(t)
                seen_track_ids.add(t.id)
            if len(focused_tracks) + len(noisy_tracks) + len(remaining_sample) >= 150:
                break

        combined_sample_entities = focused_tracks + noisy_tracks + remaining_sample
        sample_payload = [
            {
                "id": t.id,
                "title": t.title,
                "artist": t.artist_display,
                "album": t.album,
                "track_number": t.track_number,
                "year": t.original_year or t.release_year,
                "playcount": t.lastfm_playcount + t.local_playcount,
            }
            for t in combined_sample_entities
        ]

        top_artists = sorted(artists_summary, key=lambda x: x.get("track_count", 0), reverse=True)[:10]
        top_albums = sorted(albums_summary, key=lambda x: x.get("track_count", 0), reverse=True)[:10]
        library_overview = {
            "total_tracks": total_tracks,
            "total_artists": len(artists_summary),
            "total_albums": len(albums_summary),
            "top_artists": [{"artist": a["artist"], "tracks": a["track_count"]} for a in top_artists],
            "top_albums": [{"album": a["album"], "artist": a["album_artist"], "tracks": a["track_count"]} for a in top_albums],
            "matched_keywords": matched_keywords,
        }

        llm = self._build_llm()
        last_error = ""
        if llm.is_configured():
            try:
                res = llm.agent_command(
                    instruction=instruction,
                    tracks_summary=sample_payload,
                    library_overview=library_overview,
                    history=history,
                )
                parsed_actions: list[LLMCurationAction] = []
                for act in res.get("actions") or []:
                    act_type = str(act.get("action_type") or "rename")
                    parsed_actions.append(
                        LLMCurationAction(
                            track_id=int(act.get("track_id") or 0),
                            action_type=act_type,
                            current_title=str(act.get("current_title") or ""),
                            proposed_title=str(act.get("proposed_title") or ""),
                            current_artist=str(act.get("current_artist") or ""),
                            proposed_artist=str(act.get("proposed_artist") or ""),
                            current_album=str(act.get("current_album") or ""),
                            proposed_album=str(act.get("proposed_album") or ""),
                            duplicate_of_track_id=act.get("duplicate_of_track_id"),
                            old_album=str(act.get("old_album") or ""),
                            new_album=str(act.get("new_album") or ""),
                            reason=str(act.get("reason") or "LLM curated action"),
                        )
                    )
                return {
                    "reply": res.get("reply", "Processed library instruction."),
                    "actions": [a.model_dump() for a in parsed_actions],
                    "used_llm": True,
                    "engine": f"LLM ({res.get('model', llm.model)}) via {llm.base_url}",
                    "thinking": res.get("thinking", ""),
                    "debug": {
                        "status": "success",
                        "duration_ms": res.get("duration_ms", 0),
                        "model": res.get("model", llm.model),
                        "endpoint": res.get("endpoint", llm._endpoint_url()),
                        "total_library_tracks": total_tracks,
                        "evaluated_tracks_count": len(sample_payload),
                        "matched_keywords": matched_keywords,
                        "actions_count": len(parsed_actions),
                        "tokens": res.get("tokens", {}),
                    },
                }
            except Exception as exc:
                last_error = str(exc)
                logger.warning("LLM Agent call failed (%s); using fallback heuristic reasoning", exc)

        # Fallback heuristic agent if LLM is not configured or encounters a network error
        fallback_actions: list[LLMCurationAction] = []
        status_note = f"\n\n*(Note: LLM error — {last_error})*" if last_error else "\n\n*(LLM not configured: using built-in heuristic engine)*"

        if not tracks:
            reply_msg = (
                "Your library currently has 0 tracks indexed. Download some songs or an album first "
                "(via the Full Album Downloader or Last.fm Extractor), then ask me to curate or reorganize them!"
            )
            if last_error:
                reply_msg += f"\n\n[LLM Connection Notice: {last_error}]"
        elif "dedup" in lower_ins or "repeat" in lower_ins or "duplicate" in lower_ins:
            audit = self.analyze_library()
            fallback_actions = [
                LLMCurationAction(**a) for a in audit.get("actions", [])
                if a.get("action_type") == "duplicate_remove"
            ]
            reply_msg = (
                f"### 🔍 Library Deduplication Audit\n"
                f"- **Total Library Tracks**: {total_tracks}\n"
                f"- **Distinct Artists**: {len(artists_summary)}\n"
                f"- **Duplicate Candidates Detected**: {len(fallback_actions)}\n\n"
                f"Review the candidate duplicates listed below. You can uncheck any version you prefer to keep, then click **Apply Selected Agent Actions** to clean your library."
                f"{status_note}"
            )
        elif "clean" in lower_ins or "rename" in lower_ins or "album" in lower_ins or "title" in lower_ins:
            audit = self.analyze_library()
            fallback_actions = [
                LLMCurationAction(**a) for a in audit.get("actions", [])
                if a.get("action_type") == "rename"
            ]
            reply_msg = (
                f"### 🧹 Track & Album Normalization Audit\n"
                f"- **Total Tracks Evaluated**: {total_tracks}\n"
                f"- **Tracks with Noise / Non-Canonical Album**: {len(fallback_actions)}\n\n"
                f"Stripped release artifacts (e.g. `[Official Video]`, `(Remastered)`, `- Single`). "
                f"Select the proposed changes below to update SQLite and rewrite ID3v2.4 tags."
                f"{status_note}"
            )
        elif "audit" in lower_ins or "health" in lower_ins or "stat" in lower_ins or "analyze" in lower_ins:
            missing_covers = sum(1 for t in tracks if not t.has_cover)
            top_art_str = ", ".join(f"{a['artist']} ({a['track_count']} tracks)" for a in top_artists[:5]) or "None"
            reply_msg = (
                f"### 📊 Library Health & Statistics Overview\n"
                f"- **Total Tracks**: {total_tracks}\n"
                f"- **Total Distinct Artists**: {len(artists_summary)}\n"
                f"- **Total Albums**: {len(albums_summary)}\n"
                f"- **Missing Cover Art**: {missing_covers} track(s)\n"
                f"- **Top Artists**: {top_art_str}\n\n"
                f"Everything is indexed and ready for offline playback or full album streaming."
                f"{status_note}"
            )
        else:
            top_art_str = ", ".join(a["artist"] for a in top_artists[:4]) or "artists"
            reply_msg = (
                f"### 🎵 Library Overview ({total_tracks} tracks)\n"
                f"I reviewed your collection across {len(artists_summary)} artists (featuring {top_art_str}).\n\n"
                f"**Available Actions:**\n"
                f"- Click **Remove Duplicates** or **Clean Titles** above to run automated audits.\n"
                f"- Ask me specific questions about artists or albums.\n"
                f"- To enable full LLM autonomous reasoning, enter your API key in **⚙ LLM / Proxy Setup**."
                f"{status_note}"
            )

        return {
            "reply": reply_msg,
            "actions": [a.model_dump() for a in fallback_actions],
            "used_llm": False,
            "engine": "Built-in Heuristic Engine",
            "error": last_error,
            "debug": {
                "status": "fallback" if last_error else "heuristics_only",
                "error_detail": last_error,
                "total_library_tracks": total_tracks,
                "total_artists": len(artists_summary),
                "total_albums": len(albums_summary),
                "evaluated_tracks_count": total_tracks,
                "matched_keywords": matched_keywords,
                "actions_count": len(fallback_actions),
                "reason": f"Fallback due to: {last_error}" if last_error else "LLM not configured (OpenAI/Proxy API key is empty)",
            },
        }


    def apply_curation_actions(self, actions: list[LLMCurationAction]) -> dict[str, int]:
        renamed = 0
        removed_duplicates = 0

        for act in actions:
            if act.action_type == "bulk_album_rename" and act.old_album and act.new_album:
                from musiclinker.domain.models import AlbumBulkRenamePayload
                self.lib_service.bulk_rename_album(
                    AlbumBulkRenamePayload(
                        old_album=act.old_album,
                        new_album=act.new_album,
                        album_artist="",
                        remember_alias=True,
                    )
                )
                renamed += 1
                continue

            track = self.repo.get_track(act.track_id)
            if not track:
                continue

            if act.action_type == "duplicate_remove":
                mp3_file = Path(track.file_path)
                lrc_file = Path(track.lrc_path) if track.lrc_path else mp3_file.with_suffix(".lrc")
                if mp3_file.exists():
                    mp3_file.unlink(missing_ok=True)
                if lrc_file.exists():
                    lrc_file.unlink(missing_ok=True)
                self.repo.delete_track(track.id)
                removed_duplicates += 1
            elif act.action_type == "rename":
                self.lib_service.update_track_metadata(
                    track.id,
                    TrackMetadataUpdatePayload(
                        title=act.proposed_title or track.title,
                        artist_display=act.proposed_artist or track.artist_display,
                        album=act.proposed_album or track.album,
                        track_number=track.track_number,
                        release_year=track.release_year,
                        original_year=track.original_year,
                        remember_album_alias=True,
                    ),
                )
                renamed += 1

        return {"renamed": renamed, "removed_duplicates": removed_duplicates}


curation_service = CurationService()