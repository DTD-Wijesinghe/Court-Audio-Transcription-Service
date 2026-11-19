from __future__ import annotations

from pathlib import Path

_backend_package = Path(__file__).resolve().parents[1] / "backend" / "court_transcription"
if _backend_package.exists():
    __path__.append(str(_backend_package))

