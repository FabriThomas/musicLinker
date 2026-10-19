from collections import deque
from datetime import datetime, timezone
import threading
import time
from typing import Any
import uuid


class TestModeManager:
    """
    In-memory inspection buffer and telemetry logger for backend music pulling activities.
    Allows users to inspect search queries, yt-dlp candidate extraction, duration scoring,
    audio stream URLs, transcoding, and tagging stages in real-time.
    """

    __test__ = False

    def __init__(self, max_events: int = 500) -> None:
        self._enabled: bool = False
        self._events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self._lock = threading.Lock()
        self._seq = 0

    def init_from_settings(self, settings_dict: dict[str, Any]) -> None:
        val = settings_dict.get("test_mode")
        if val is not None:
            self._enabled = bool(val)

    def is_enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._enabled = bool(enabled)
            self._record_internal(
                category="system",
                title=f"Test Mode {'ENABLED' if self._enabled else 'DISABLED'}",
                details={"enabled": self._enabled},
                level="info",
            )

    def record_event(
        self,
        category: str,
        title: str,
        details: dict[str, Any] | None = None,
        level: str = "info",
    ) -> None:
        """Record an event into the inspection buffer."""
        with self._lock:
            self._record_internal(category=category, title=title, details=details, level=level)

    def _record_internal(
        self,
        category: str,
        title: str,
        details: dict[str, Any] | None = None,
        level: str = "info",
    ) -> None:
        self._seq += 1
        now = datetime.now(timezone.utc)
        event = {
            "id": f"evt_{self._seq}_{uuid.uuid4().hex[:6]}",
            "seq": self._seq,
            "timestamp": time.time(),
            "time_str": now.strftime("%H:%M:%S"),
            "iso_time": now.isoformat(),
            "category": category,
            "title": title,
            "details": details or {},
            "level": level,
        }
        self._events.append(event)

    def get_events(
        self,
        since_seq: int = 0,
        category: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._events)
        if since_seq > 0:
            items = [e for e in items if e["seq"] > since_seq]
        if category and category.lower() != "all":
            cat_lower = category.lower()
            items = [e for e in items if e["category"].lower() == cat_lower]
        if limit and len(items) > limit:
            items = items[-limit:]
        return items

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
            self._record_internal(
                category="system",
                title="Test Mode inspection logs cleared.",
                details={},
                level="info",
            )


test_mode_manager = TestModeManager()


def log_test_event(
    category: str,
    title: str,
    details: dict[str, Any] | None = None,
    level: str = "info",
) -> None:
    test_mode_manager.record_event(category=category, title=title, details=details, level=level)
