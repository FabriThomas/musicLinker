"""OpenAI-compatible LLM API / Proxy client for track deduplication, title cleaning, and album canonicalization."""

from __future__ import annotations

import json
import re
from typing import Any

import requests

from musiclinker.core.logging_config import logger


class LLMClient:
    """
    Calls any OpenAI-compatible API endpoint or user-supplied reverse proxy URL
    (e.g. OpenAI, OpenRouter, Gemini OpenAI-compat, Groq, Ollama, or custom proxy)
    to deduplicate repeated tracks (e.g. Remastered vs Radio Edit) and canonicalize track/album names.
    """

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
        """
        Send a batch of tracks to the LLM proxy and return structured curation instructions:
          - `track_id`: int
          - `action_type`: `'rename' | 'duplicate_remove' | 'keep'`
          - `proposed_title`: clean canonical song title
          - `proposed_artist`: clean artist string
          - `proposed_album`: proper canonical studio album name
          - `duplicate_of_track_id`: int | null (when two versions of the same song exist)
          - `reason`: short explanation
        """
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