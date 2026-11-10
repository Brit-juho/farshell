"""Claude usage from the official statusline input — hotswap H2.

Claude Code hands every statusLine command a JSON document that carries, for the account that
session runs on and after the session's first reply, `rate_limits.five_hour|seven_day` with
`used_percentage` (0-100) and `resets_at` (Unix seconds). The wrapper `bin/fsh-statusline`, placed in
a managed profile's `settings.json` by the settings sync, keeps the last such document per profile at
`<VT_STATE_DIR>/usage/statusline/<profile-id>.json` (0600, atomically replaced). This module only
READS that file: a missing, foreign-owned, oversized or malformed file is "no value", never 0%.

The file holds the whole input (cwd and transcript path included), so nothing but the two windows
leaves this module. A window whose reset time has passed is dropped (its percentage describes a window
that no longer exists). The file's mtime is the time of the value.
"""
from __future__ import annotations

import json
import math
import os
import stat
from datetime import datetime, timezone

import vt_paths

SOURCE = "statusline"
MAX_BYTES = 256 * 1024
WINDOWS = (("five_hour", "5h"), ("seven_day", "weekly"))


def directory():
    return vt_paths.state_dir() / "usage" / "statusline"


def path(profile_id):
    return directory() / f"{profile_id}.json"


def _number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def _window(raw, wid, label, now, updated_at):
    if not isinstance(raw, dict):
        return None
    pct = _number(raw.get("used_percentage"))
    if pct is None or not 0 <= pct <= 100:
        return None
    reset = _number(raw.get("resets_at"))
    if reset is not None:
        if reset > 1e11:        # milliseconds
            reset /= 1000
        if reset <= now:        # that window is over
            return None
    return {"id": wid, "label": label, "pct": float(pct),
            "resets_at": datetime.fromtimestamp(reset, timezone.utc).isoformat() if reset is not None else None,
            "resets_in_sec": max(0, int(reset - now)) if reset is not None else None,
            "source": SOURCE, "updated_at": updated_at}


def read(profile_id, now):
    """`{windows, updated_at, source}` or None when the profile has no usable value."""
    try:
        file = path(profile_id)
        info = os.lstat(file)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > MAX_BYTES:
            return None
        with open(file, "rb") as stream:
            value = json.loads(stream.read(MAX_BYTES + 1))
    except (OSError, ValueError):
        return None
    limits = value.get("rate_limits") if isinstance(value, dict) else None
    if not isinstance(limits, dict):
        return None
    windows = [w for key, label in WINDOWS if (w := _window(limits.get(key), key, label, now, info.st_mtime))]
    return {"windows": windows, "updated_at": info.st_mtime, "source": SOURCE} if windows else None
