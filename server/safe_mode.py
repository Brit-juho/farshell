"""안전 모드 — 위험 명령 사전 차단 (1차 방어선).

VT_SAFE_MODE=1 환경변수로 활성화. 모바일 공개 터널 사용 시 권장.

한계: 우회 가능 (r''m, base64, 스크립트 파일 등). 완벽 방어 아님.
"""

import os
import re
from typing import Optional

DANGEROUS_PATTERNS = [
    # rm -rf 시스템 루트/디렉토리 (사용자 디렉토리는 허용)
    (re.compile(r"\brm\s+-[rRf]+\s+(/(\s|$|\*)|/(etc|usr|var|bin|sbin|lib|System|Library|Applications|home|root|opt|boot)\b)"), "rm -rf 시스템 경로"),
    (re.compile(r"\bsudo\b"),                            "sudo"),
    (re.compile(r"\bgit\s+push\s+(-f|--force)\b"),       "git force push"),
    (re.compile(r"\bdd\s+if=.+\s+of=/dev/"),             "dd to device"),
    (re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"), "fork bomb"),
    (re.compile(r">\s*/dev/(sda|nvme|disk)"),            "block device write"),
    (re.compile(r"\bchmod\s+-R\s+777\s+/"),              "chmod -R 777 /"),
    (re.compile(r"\bmkfs\."),                            "mkfs (filesystem format)"),
    (re.compile(r"\b(shutdown|reboot|halt|poweroff)\b"), "system shutdown"),
    (re.compile(r"\b(curl|wget)\s+[^|]*\|\s*(bash|sh)"), "remote shell exec"),
    (re.compile(r":\s*>\s*~/?\.ssh/"),                   "ssh dir wipe"),
]


def is_enabled() -> bool:
    return os.environ.get("VT_SAFE_MODE", "").strip() in ("1", "true", "yes")


def check(cmd: str) -> tuple[bool, Optional[str]]:
    """명령어 검사.

    Returns:
        (is_safe, reason) — is_safe=False면 reason은 차단 사유.
    """
    if not is_enabled():
        return True, None
    if not cmd or not cmd.strip():
        return True, None

    # 주석 제거 (간단한 형태)
    cmd_check = cmd.split("#", 1)[0]

    for pat, label in DANGEROUS_PATTERNS:
        if pat.search(cmd_check):
            return False, label
    return True, None
