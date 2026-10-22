"""Native apiKeyHelper: private profile file to stdout, never an HTTP response."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import fsguard

FILENAME = '.farshell-provider.json'


def read(root):
    path = fsguard.profile_file(root, FILENAME)
    if not path.exists():
        return None
    if path.stat().st_mode & 0o077:
        raise ValueError('Private provider file permissions must be 0600')
    value = json.loads(fsguard.read_profile_bytes(root, FILENAME, 128 * 1024))
    if not isinstance(value, dict) or value.get('version') != 1 or not isinstance(value.get('env'), dict):
        raise ValueError('Invalid private provider file')
    return value


def main():
    try:
        if len(sys.argv) != 2:
            return 1
        value = read(Path(sys.argv[1]))
        key = value.get('api_key') if value else None
        if not isinstance(key, str) or not key or len(key) > 32768 or any(c.isspace() or ord(c) < 33 or ord(c) == 127 for c in key):
            return 1
        sys.stdout.write(key)
        return 0
    except (OSError, ValueError, fsguard.FsDenied):
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
