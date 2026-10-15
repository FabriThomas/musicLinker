"""OpenAI-compatible LLM API / Proxy client for track deduplication, title cleaning, and album canonicalization."""

from __future__ import annotations

import json
import re
import time
from typing import Any

import requests


class LLMClient:
    """Calls any OpenAI-compatible API endpoint or reverse proxy URL for track deduplication and album canonicalization."""

    def __init__(self, base_url: str, api_key: str = "", model: str = "gpt-4o-mini", timeout: float = 40.0) -> None:
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

        try:
            resp = requests.post(self._endpoint_url(), headers=headers, json=body, timeout=self.timeout)
            resp.raise_for_status()
        except requests.exceptions.RequestException as req_err:
            raise self._translate_request_error(req_err) from req_err

        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        thinking, clean_content = self._extract_thinking(content)
        parsed = self._extract_json(clean_content)
        if isinstance(parsed, dict) and isinstance(parsed.get("actions"), list):
            return parsed["actions"]
        if isinstance(parsed, list):
            return parsed
        return []

    def agent_command(
        self,
        instruction: str,
        tracks_summary: list[dict[str, Any]],
        library_overview: dict[str, Any] | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """
        Interactive AI Music Agent: accepts user prompt/instruction, reasons over library context,
        and produces both thoughtful conversational answers and structured tool actions.
        Supported action types:
          - 'rename': {"track_id": int, "proposed_title": str, "proposed_artist": str, "proposed_album": str, "reason": str}
          - 'duplicate_remove': {"track_id": int, "duplicate_of_track_id": int, "reason": str}
          - 'bulk_album_rename': {"old_album": str, "new_album": str, "artist": str, "reason": str}
        """
        system_prompt = (
            "You are MusicLinker AI Agent, an autonomous music discographer, audio archivist, and library curator.\n"
            "You have direct awareness of the user's music library and playback collection.\n"
            "Guidelines for Proper Answers:\n"
            "1. Answer user requests with depth, clarity, and musical intelligence. If the user asks a question, wants recommendations, discography facts, or playlist guidance, provide a structured, formatted Markdown reply (using bullet points, bold titles, and concise explanations).\n"
            "2. When proposing library modifications (deduplication, fixing titles, assigning real album names, removing noise like '[Official Video]' or '2011 Remaster'), explain WHY each change is suggested.\n"
            "3. If the user's request requires library changes, populate the 'actions' list. If the user is just asking a question, asking for recommendations, or no tracks need modification, 'actions' should be an empty list [].\n"
            "Return a JSON object with this exact schema:\n"
            "{\n"
            '  "reply": "Comprehensive, well-formatted response with insights, advice, or summary of actions for the user",\n'
            '  "actions": [\n'
            '    {\n'
            '      "track_id": int,\n'
            '      "action_type": "rename" | "duplicate_remove" | "bulk_album_rename",\n'
            '      "proposed_title": str (optional),\n'
            '      "proposed_artist": str (optional),\n'
            '      "proposed_album": str (optional),\n'
            '      "duplicate_of_track_id": int or null,\n'
            '      "old_album": str (optional for bulk_album_rename),\n'
            '      "new_album": str (optional for bulk_album_rename),\n'
            '      "reason": str\n'
            '    }\n'
            '  ]\n'
            "}"
        )

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        messages = [{"role": "system", "content": system_prompt}]
        if history:
            for msg in history[-6:]:
                messages.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})

        user_payload: dict[str, Any] = {
            "user_instruction": instruction,
            "library_sample": tracks_summary,
        }
        if library_overview:
            user_payload["library_overview"] = library_overview

        messages.append({"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)})

        body = {
            "model": self.model,
            "temperature": 0.2,
            "messages": messages,
        }

        start_time = time.time()
        try:
            resp = requests.post(self._endpoint_url(), headers=headers, json=body, timeout=self.timeout)
            resp.raise_for_status()
        except requests.exceptions.RequestException as req_err:
            raise self._translate_request_error(req_err) from req_err

        duration_ms = round((time.time() - start_time) * 1000)
        data = resp.json()
        raw_choice = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        tokens = data.get("usage", {})

        thinking, clean_content = self._extract_thinking(raw_choice)
        try:
            parsed = self._extract_json(clean_content)
        except Exception:
            # Model responded with conversational text instead of valid JSON
            return {
                "reply": clean_content.strip() or raw_choice.strip(),
                "actions": [],
                "thinking": thinking,
                "duration_ms": duration_ms,
                "model": self.model,
                "endpoint": self._endpoint_url(),
                "tokens": tokens,
            }

        if isinstance(parsed, dict):
            return {
                "reply": str(parsed.get("reply") or clean_content.strip() or "Processed library instruction."),
                "actions": parsed.get("actions") or [],
                "thinking": thinking,
                "duration_ms": duration_ms,
                "model": self.model,
                "endpoint": self._endpoint_url(),
                "tokens": tokens,
            }

        return {
            "reply": clean_content.strip() or "Processed library instruction.",
            "actions": parsed if isinstance(parsed, list) else [],
            "thinking": thinking,
            "duration_ms": duration_ms,
            "model": self.model,
            "endpoint": self._endpoint_url(),
            "tokens": tokens,
        }

    @staticmethod
    def _extract_thinking(raw_text: str) -> tuple[str, str]:
        """Extract reasoning from <think>...</think> tags commonly emitted by DeepSeek/Qwen models."""
        thinking = ""
        clean_text = raw_text
        think_match = re.search(r"<think>(.*?)</think>", raw_text, re.DOTALL | re.IGNORECASE)
        if think_match:
            thinking = think_match.group(1).strip()
            clean_text = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL | re.IGNORECASE).strip()
        return thinking, clean_text

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

    def _translate_request_error(self, exc: requests.exceptions.RequestException) -> Exception:
        """Translates low-level HTTP/network exceptions into clean, diagnostic error messages."""
        endpoint = self._endpoint_url()
        if isinstance(exc, (requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError)):
            return ConnectionError(
                f"Failed to connect to LLM server at '{endpoint}'. "
                "Ensure your local LLM or reverse proxy is running and reachable."
            )
        if isinstance(exc, requests.exceptions.Timeout):
            return TimeoutError(
                f"LLM request timed out after {self.timeout}s waiting for '{endpoint}'. "
                "The target model may be overloaded or generating a very large response."
            )
        if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None:
            status = exc.response.status_code
            if status == 401:
                return PermissionError(
                    f"LLM Authentication Failed (HTTP 401): Invalid API key or proxy password for '{endpoint}'."
                )
            if status == 404:
                return ValueError(
                    f"LLM Not Found (HTTP 404): Model '{self.model}' or endpoint '{endpoint}' does not exist."
                )
            if status == 429:
                return RuntimeError(
                    f"LLM Rate Limit / Quota Exceeded (HTTP 429) at '{endpoint}'. Please check your plan or retry later."
                )
            if status in (502, 503, 504):
                return ConnectionError(
                    f"LLM Gateway Error (HTTP {status}) at '{endpoint}': Target upstream server is unavailable."
                )
            return RuntimeError(f"LLM Error (HTTP {status}) from '{endpoint}': {exc.response.text[:200]}")
        return RuntimeError(f"LLM Network Error ({type(exc).__name__}) at '{endpoint}': {exc}")