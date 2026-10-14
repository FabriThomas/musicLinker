"""UTF-8 safe logging setup preventing Windows CP1252 console crashes."""

from __future__ import annotations

import io
import logging
import sys
from logging.handlers import RotatingFileHandler

from musiclinker.core.paths import PATHS


class NullStream(io.TextIOBase):
    """Safe fallback stream when running in windowed mode without a console attached."""

    def write(self, s: str) -> int:
        return len(s)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False


def configure_logging(level: int = logging.INFO) -> logging.Logger:
    """Configure root and application loggers with UTF-8 encoding and file rotation."""
    # Ensure stdout and stderr are never None in GUI / windowed mode
    if sys.stdout is None:
        sys.stdout = NullStream()
    if sys.stderr is None:
        sys.stderr = NullStream()

    # Reconfigure standard streams on Windows to prevent CP1252 UnicodeEncodeError
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
        elif stream is not None and hasattr(stream, "buffer"):
            try:
                setattr(
                    sys,
                    stream_name,
                    io.TextIOWrapper(stream.buffer, encoding="utf-8", errors="replace"),
                )
            except Exception:
                pass

    logger = logging.getLogger("musiclinker")
    if logger.handlers:
        return logger

    logger.setLevel(level)
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        PATHS.logs_dir / "musiclinker.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    if sys.stdout is not None and not isinstance(sys.stdout, NullStream):
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    return logger


logger = configure_logging()