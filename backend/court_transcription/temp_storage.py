from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Iterable

from .config import (
    TEMP_STORAGE_MIN_FREE_GB,
    TEMP_STORAGE_RESERVE_MULTIPLIER,
    TEMP_STORAGE_ROOT,
    TEMP_STORAGE_STALE_HOURS,
    TEMP_STORAGE_WARNING_FREE_GB,
)

GIB = 1024 ** 3


def _root() -> Path:
    root = TEMP_STORAGE_ROOT.resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def _inside_root(path: Path) -> Path:
    resolved = path.resolve()
    resolved.relative_to(_root())
    return resolved


def create_job_workspace(job_id: str) -> Path:
    if not job_id or not all(ch.isalnum() or ch in "-_" for ch in job_id):
        raise ValueError("Invalid temporary workspace id")
    workspace = _inside_root(_root() / job_id)
    workspace.mkdir(mode=0o700)
    (workspace / ".owner-pid").write_text(str(os.getpid()), encoding="ascii")
    for name in ("original", "processed", "chunks", "batch"):
        (workspace / name).mkdir(mode=0o700)
    return workspace


def safe_unlink(path: str | Path | None) -> bool:
    if not path:
        return False
    target = _inside_root(Path(path))
    try:
        target.unlink(missing_ok=True)
        return True
    except IsADirectoryError:
        return False


def cleanup_job_workspace(path: str | Path | None) -> int:
    if not path:
        return 0
    target = _inside_root(Path(path))
    if target == _root() or not target.exists():
        return 0
    reclaimed = directory_size(target)
    shutil.rmtree(target)
    return reclaimed


def directory_size(path: Path) -> int:
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file() and not entry.is_symlink():
                total += entry.stat().st_size
    except (FileNotFoundError, PermissionError):
        pass
    return total


def cleanup_stale_workspaces(active_job_ids: Iterable[str] = (), stale_hours: int | None = None) -> dict[str, int]:
    root = _root()
    active = set(active_job_ids)
    cutoff = time.time() - 3600 * int(stale_hours or TEMP_STORAGE_STALE_HOURS)
    removed = reclaimed = skipped = 0
    for child in root.iterdir():
        if child.is_symlink() or not child.is_dir():
            continue
        if child.name in active or any(child.name.startswith(f"{job_id}-") for job_id in active):
            skipped += 1
            continue
        try:
            if child.stat().st_mtime >= cutoff:
                continue
            owner_file = child / ".owner-pid"
            if owner_file.is_file():
                try:
                    owner_pid = int(owner_file.read_text(encoding="ascii").strip())
                    os.kill(owner_pid, 0)
                    skipped += 1
                    continue
                except (ValueError, ProcessLookupError, PermissionError, OSError):
                    pass
            reclaimed += cleanup_job_workspace(child)
            removed += 1
        except (FileNotFoundError, PermissionError, ValueError):
            skipped += 1
    return {"removed": removed, "reclaimedBytes": reclaimed, "skipped": skipped}


def disk_metrics() -> dict[str, object]:
    root = _root()
    usage = shutil.disk_usage(root)
    free_gb = usage.free / GIB
    if free_gb < TEMP_STORAGE_MIN_FREE_GB:
        status = "critical"
    elif free_gb < TEMP_STORAGE_WARNING_FREE_GB:
        status = "warning"
    else:
        status = "healthy"
    return {
        "path": str(root),
        "totalBytes": usage.total,
        "usedBytes": usage.used,
        "freeBytes": usage.free,
        "totalGb": round(usage.total / GIB, 2),
        "usedGb": round(usage.used / GIB, 2),
        "freeGb": round(free_gb, 2),
        "usedPercent": round((usage.used / usage.total * 100) if usage.total else 0, 1),
        "status": status,
        "minimumFreeGb": TEMP_STORAGE_MIN_FREE_GB,
        "warningFreeGb": TEMP_STORAGE_WARNING_FREE_GB,
        "temporaryBytes": directory_size(root),
    }


def has_capacity(upload_bytes: int = 0) -> tuple[bool, dict[str, object]]:
    metrics = disk_metrics()
    required = max(int(TEMP_STORAGE_MIN_FREE_GB * GIB), int(max(0, upload_bytes) * TEMP_STORAGE_RESERVE_MULTIPLIER))
    metrics["requiredFreeBytes"] = required
    metrics["reserveMultiplier"] = TEMP_STORAGE_RESERVE_MULTIPLIER
    return int(metrics["freeBytes"]) >= required, metrics
