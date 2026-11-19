from __future__ import annotations

import json

from .temp_storage import cleanup_stale_workspaces, disk_metrics


def main() -> int:
    result = cleanup_stale_workspaces()
    result["storage"] = disk_metrics()
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
