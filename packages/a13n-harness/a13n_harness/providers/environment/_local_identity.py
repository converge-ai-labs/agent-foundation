from __future__ import annotations

import hashlib
import json
import os
import platform
from pathlib import Path

from pydantic import JsonValue


def local_backing_identity(*, provider_key: str, roots: tuple[Path, ...], policy: JsonValue) -> str | None:
    """Observe Host filesystem backing during preparation, never during discovery.

    This is workspace continuity evidence, not a content digest or a race-free
    filesystem lease. Directory timestamps deliberately do not participate:
    normal writes do not replace the backing target. Missing file IDs mean
    stable backing evidence is unavailable.
    """
    try:
        host = platform.node()
        if not host:
            return None
        evidence = []
        for path in roots:
            root = path.resolve(strict=True)
            info = root.stat()
            filesystem = Path(root.anchor).stat()
            if not root.is_dir() or info.st_ino == 0 or filesystem.st_ino == 0:
                return None
            evidence.append((str(root), info.st_dev, info.st_ino, filesystem.st_dev, filesystem.st_ino))
    except OSError:
        return None
    payload = json.dumps(
        {"provider": provider_key, "host": [os.name, host], "roots": evidence, "policy": policy},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return "local-v1-" + hashlib.sha256(payload.encode()).hexdigest()
