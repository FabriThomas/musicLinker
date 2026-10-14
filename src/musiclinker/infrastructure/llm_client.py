"""OpenAI-compatible LLM API / Proxy client for track deduplication, title cleaning, and album canonicalization."""

from __future__ import annotations

import json
import re
from typing import Any

import requests


class LLMClient:
    """Calls any OpenAI-compatible API endpoint or reverse proxy URL for track deduplication and album canonicalization."""

    def __init__(self, base_url: str, api_key: str = "", model: str = "gpt-4o-mini", timeout: float = 35.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key.strip()
        self.model = model.strip() or "gpt-4o-mini"
        self.timeout = timeout

    def is_configured(self) -> bool:
        return bool(self.base_url) and (
            bool(self.api_key)
            or "localhost" in self.base_url
            or "127.0.0.1" in self.base_url
            or not self.base_url.startswith("https://api.openai.com")
        )

    def _endpoint_url(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        if self.base_url.endswith("/v1"):
            return f"{self.base_url}/chat/completions"
        return f"{self.base_url}/v1/chat/completions"

    def curate_tracks_json(self, tracks_payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
        system_prompt = (
            "You are an expert music discography curator and audio library deduplicator.\n"
            "Given a JSON list of tracks (each with id, title, artist, album, playcount), analyze them and return ONLY a valid JSON object with a single key \"actions\" containing an array of objects.\n"
            "Rules:\n"
            "1. Detect repeated/duplicate versions of the same song by the same artist (e.g. Remastered, Radio Edit, Deluxe, Live, Single Version, or slight spelling variants). Keep the version with the highest playcount (or lowest id) and mark the redundant version(s) with action_type='duplicate_remove' and duplicate_of_track_id=<kept_id>.\n"
            "2. Clean messy track titles (strip '(Official Video)', '- 2011 Remaster', '[Explicit]', trailing release noise) and fix wrong or messy album names (e.g. replace '<Song> - Single', 'Greatest Hits (Deluxe Edition)', or 'Unknown Album' with the proper studio album name).\n"
            "3. Only emit entries in \"actions\" for tracks that need renaming/album-fixing (action_type='rename') or duplicate removal (action_type='duplicate_remove').\n"
            "Schema per item: {\"track_id\": int, \"action_type\": \"rename\"|\"duplicate_remove\", \"proposed_title\": str, \"proposed_artist\": str, \"proposed_album\": str, \"duplicate_of_track_id\": int|null, \"reason\": str}"
        )

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        body = {
            "model": self.model,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps({"tracks": tracks_payload}, ensure_ascii=False)},
            ],
        }

        resp = requests.post(self._endpoint_url(), headers=headers, json=body, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        parsed = self._extract_json(content)
        if isinstance(parsed, dict) and isinstance(parsed.get("actions"), list):
            return parsed["actions"]
        if isinstance(parsed, list):
            return parsed
        return []

    def agent_command(
        self,
        instruction: str,
        tracks_summary: list[dict[str, Any]],
        history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """
        Interactive AI Music Agent: accepts user prompt/instruction, reasons over library context,
        and produces both conversational reasoning/reply and structured tool actions.
        Supported action types:
          - 'rename': {"track_id": int, "proposed_title": str, "proposed_artist": str, "proposed_album": str, "reason": str}
          - 'duplicate_remove': {"track_id": int, "duplicate_of_track_id": int, "reason": str}
          - 'bulk_album_rename': {"old_album": str, "new_album": str, "artist": str, "reason": str}
        """
        system_prompt = (
            "You are MusicLinker AI Agent, an autonomous music library curator, discographer, and audio organizer.\n"
            "You have direct awareness of the user's music library tracks.\n"
            "When the user speaks to you or gives an instruction (e.g., 'group live tracks', 'fix missing albums', 'deduplicate songs', 'analyze my collection'):\n"
            "1. Reason carefully about the instruction and the provided tracks.\n"
            "2. Produce a clear, friendly, expert reply summarizing what you did or recommend.\n"
            "3. If any track renames, album fixes, or duplicate removals are warranted, emit structured 'actions'.\n"
            "Return ONLY a valid JSON object matching this schema:\n"
            "{\n"
            '  "reply": "Your explanation, insights, and summary of planned actions for the user",\n'
            '  "actions": [\n'
            '    {\n'
            '      "track_id": int (required for rename/duplicate_remove, or 0 if bulk),\n'
            '      "action_type": "rename" | "duplicate_remove" | "bulk_album_rename",\n'
            '      "proposed_title": str (optional),\n'
            '      "proposed_artist": str (optional),\n'
            '      "proposed_album": str (optional),\n'
            '      "duplicate_of_track_id": int or null,\n'
            '      "old_album": str (optional for bulk_album_rename),\n'
            '      "new_album": str (optional for bulk_album_rename),\n'
            '      "reason": str\n'
            "    }\n"
            "  ]\n"
            "}"
        )

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        messages = [{"role": "system", "content": system_prompt}]
        if history:
            for msg in history[-6:]:
                messages.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})

        user_content = json.dumps(
            {
                "user_instruction": instruction,
                "library_sample": tracks_summary[:120],
            },
            ensure_ascii=False,
        )
        messages.append({"role": "user", "content": user_content})

        body = {
            "model": self.model,
            "temperature": 0.2,
            "messages": messages,
        }

        resp = requests.post(self._endpoint_url(), headers=headers, json=body, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        parsed = self._extract_json(content)
        if isinstance(parsed, dict):
            return {
                "reply": str(parsed.get("reply") or "Processed library instruction."),
                "actions": parsed.get("actions") or [],
            }
        return {"reply": content, "actions": []}

    @staticmethod
    def _extract_json(raw_text: str) -> Any:
        text = raw_text.strip()
        fence_match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
        if fence_match:
            text = fence_match.group(1).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                return json.loads(text[start : end + 1])
            raise