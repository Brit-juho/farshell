"""S3: 인증 없이 서버를 띄우려 하면 무작위 비밀번호를 만든다(터미널일 때만), 아니면 띄우지 않는다.

예전에는 공개 터널(--network all)만 거부했고 `fsh voice`·`fsh clip`·`--network lan/tailscale/localhost`는
인증 없이 떴다. bin/fsh 를 통째로 돌리지 않고 lib/auth_bootstrap.sh 만 불러 시험한다.
"""
import os
import pty
import re
import subprocess
import sys
from pathlib import Path

import auth

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable


def _script(cfg: Path) -> str:
    return (f'VT_DIR="{ROOT}"; VT_PYTHON="{PY}"; export VT_CONFIG="{cfg}"; '
            f'. "{ROOT}/lib/vt_env.sh"; vt_env_load "$VT_CONFIG"; '  # bin/fsh 처럼 설정 파일을 먼저 읽는다
            f'_t() {{ printf "%s" "$1"; }}; . "{ROOT}/lib/auth_bootstrap.sh"; '
            '_vt_ensure_auth; echo "rc=$?"; [ -n "$VT_AUTH_PASSWORD_HASH" ] && echo exported')


def _env(tmp_path: Path) -> dict:
    return {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "VT_STATE_DIR": str(tmp_path / "vt")}


def _run_tty(script: str, env: dict) -> str:
    """stdout 을 가짜 터미널에 붙여 실행한다(`[ -t 1 ]` 이 참)."""
    master, slave = pty.openpty()
    p = subprocess.Popen(["bash", "-c", script], stdout=slave, stderr=slave, stdin=subprocess.DEVNULL, env=env)
    os.close(slave)
    chunks = []
    while True:
        try:
            data = os.read(master, 4096)
        except OSError:
            break
        if not data:
            break
        chunks.append(data)
    p.wait(timeout=60)
    os.close(master)
    return b"".join(chunks).decode("utf-8", "replace")


def test_generate_password_cli_shape():
    out = subprocess.run([PY, "-m", "auth", "generate-password"], cwd=ROOT / "server",
                         capture_output=True, text=True, check=True).stdout.splitlines()
    pw, hashed = out
    assert re.fullmatch(r"[a-hjkmnp-z2-9]{5}(-[a-hjkmnp-z2-9]{5}){3}", pw)
    assert auth.verify_password(pw, hashed)


def test_no_tty_refuses_and_writes_nothing(tmp_path):
    cfg = tmp_path / "vt.env"
    r = subprocess.run(["bash", "-c", _script(cfg)], env=_env(tmp_path), capture_output=True, text=True, timeout=60)
    assert "rc=1" in r.stdout
    assert "fsh password" in r.stderr
    assert not cfg.exists() or "VT_AUTH_PASSWORD_HASH" not in cfg.read_text()


def test_tty_generates_once_and_stores_only_the_hash(tmp_path):
    cfg = tmp_path / "vt.env"
    out = _run_tty(_script(cfg), _env(tmp_path))
    assert "rc=0" in out and "exported" in out
    pw = re.search(r"\b([a-z2-9]{5}(?:-[a-z2-9]{5}){3})\b", out).group(1)
    text = cfg.read_text()
    assert pw not in text, "원문이 설정 파일에 남으면 안 된다"
    hashed = re.search(r"^VT_AUTH_PASSWORD_HASH=['\"]?([^'\"\n]+)", text, re.M).group(1)
    assert auth.verify_password(pw, hashed)
    assert re.search(r"^VT_AUTH_SESSION_KEY=", text, re.M)
    assert cfg.stat().st_mode & 0o777 == 0o600
    # 이미 인증이 있으면 다시 만들지 않는다
    again = _run_tty(_script(cfg), _env(tmp_path))
    assert "rc=0" in again and pw not in again and cfg.read_text() == text
