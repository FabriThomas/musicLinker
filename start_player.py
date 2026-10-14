"""Bootstrap launcher for MusicLinker Desktop Application."""

from __future__ import annotations

import io
from pathlib import Path
import sys

# In GUI/windowed mode on Windows, standard streams can be None
class _NullStream(io.TextIOBase):
    def write(self, s: str) -> int:
        return len(s)
    def flush(self) -> None:
        pass
    def isatty(self) -> bool:
        return False

if sys.stdout is None:
    sys.stdout = _NullStream()
if sys.stderr is None:
    sys.stderr = _NullStream()

SRC_DIR = Path(__file__).resolve().parent / "src"
if SRC_DIR.exists() and str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from musiclinker.entrypoints.desktop import main

if __name__ == "__main__":
    main()