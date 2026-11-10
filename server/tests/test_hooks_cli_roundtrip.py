"""`fsh hooks install` / `fsh hooks status` 왕복 — 격리 HOME에서 실제 bin/fsh를 돌린다.

PostToolUseFailure 추가(12→13)로 이미 12개만 설치된 사용자가 「미등록」으로 보였다가
`fsh hooks install` 한 번으로 13개가 되는 길을 고정한다. 실제 ~/.claude·~/.codex는 건드리지 않는다.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASH = "/bin/bash" if os.path.exists("/bin/bash") else "bash"


def _fsh(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    cfg = tmp_path / "vt.env"
    if not cfg.exists():
        cfg.write_text("VT_LANG='en'\n")
        cfg.chmod(0o600)
    env = {
        "PATH": os.environ["PATH"], "HOME": str(tmp_path), "TERM": "dumb",
        "VT_CONFIG": str(cfg), "VT_PYTHON": sys.executable, "VT_DIR": str(ROOT),
        "CLAUDE_CONFIG_DIR": str(tmp_path / "claude"), "VT_CODEX_HOME": str(tmp_path / "codex"),
    }
    return subprocess.run([BASH, str(ROOT / "bin" / "fsh"), *args], env=env, capture_output=True,
                          text=True, timeout=60, cwd=str(ROOT), stdin=subprocess.DEVNULL)


def test_hooks_install_status_roundtrip_from_twelve(tmp_path):
    (tmp_path / "claude").mkdir()
    (tmp_path / "codex").mkdir()
    assert _fsh(tmp_path, "hooks", "install").returncode == 0
    settings = tmp_path / "claude" / "settings.json"
    data = json.loads(settings.read_text())
    assert "PostToolUseFailure" in data["hooks"] and len(data["hooks"]) == 13
    ok = _fsh(tmp_path, "hooks", "status").stdout
    assert "PostToolUseFailure" in ok and "✗" not in ok.split("Codex")[0]

    # 12개만 설치된 옛 설정으로 되돌린다.
    del data["hooks"]["PostToolUseFailure"]
    settings.write_text(json.dumps(data))
    legacy = _fsh(tmp_path, "hooks", "status").stdout
    claude_part = legacy.split("Codex")[0]
    assert "✗ PostToolUseFailure" in claude_part and claude_part.count("✗") == 1
    assert "fsh hooks install" in legacy

    assert _fsh(tmp_path, "hooks", "install").returncode == 0
    assert "PostToolUseFailure" in json.loads(settings.read_text())["hooks"]
    assert (tmp_path / "claude" / "settings.json.vtbak").exists()
    assert "✗" not in _fsh(tmp_path, "hooks", "status").stdout.split("Codex")[0]
