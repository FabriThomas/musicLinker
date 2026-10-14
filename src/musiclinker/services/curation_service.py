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

    def apply_curation_actions(self, actions: list[LLMCurationAction]) -> dict[str, int]:
        renamed = 0
        removed_duplicates = 0

        for act in actions:
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
                        title=act.proposed_title,
                        artist_display=act.proposed_artist,
                        album=act.proposed_album,
                        track_number=track.track_number,
                        release_year=track.release_year,
                        original_year=track.original_year,
                        remember_album_alias=True,
                    ),
                )
                renamed += 1

        return {"renamed": renamed, "removed_duplicates": removed_duplicates}


curation_service = CurationService()