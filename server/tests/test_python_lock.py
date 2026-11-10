"""파이썬 해시 잠금(locks/*.txt) — 저장소의 잠금이 온전한지, 점검기가 어긋남을 잡는지, 설치 경로가 잠금만 쓰는지."""
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

import updater

pytest.importorskip("packaging")

ROOT = Path(__file__).resolve().parent.parent.parent


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_python_lock", ROOT / "scripts" / "check_python_lock.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


chk = _load_checker()

_H = "--hash=sha256:" + "a" * 64


def _tree(tmp_path, core_req="alpha>=1.0\n", core_lock=None, voice_req="beta>=2\n", voice_lock=None,
          dev_req="gamma>=3\n", dev_lock=None):
    (tmp_path / "locks").mkdir()
    ok = lambda n, v: f"{n}=={v} \\\n    {_H}\n"  # noqa: E731
    files = {
        "requirements-core.txt": core_req, "requirements-voice.txt": voice_req, "requirements-dev.txt": dev_req,
        "locks/core.txt": core_lock if core_lock is not None else ok("alpha", "1.2"),
        "locks/voice.txt": voice_lock if voice_lock is not None else ok("beta", "2.1"),
        "locks/dev.txt": dev_lock if dev_lock is not None else ok("gamma", "3.0"),
    }
    for k, v in files.items():
        (tmp_path / k).write_text(v)
    return tmp_path


# ── 저장소의 실제 잠금 ──────────────────────────────────────────────────────

def test_repo_lock_is_consistent():
    assert chk.check(ROOT) == []


def test_repo_lock_every_line_is_hashed_pin():
    for prof in chk.PROFILES:
        entries, problems = chk.parse_lock(ROOT / "locks" / f"{prof}.txt")
        assert problems == [] and entries
        assert all(e["hashes"] for e in entries)


def test_lock_python_floor_matches_install_gate():
    """잠금의 하한 파이썬(lock_python.sh)이 install.sh 의 최소 버전·CI 매트릭스 하한과 같다."""
    floor = re.search(r'PYTHON_FLOOR="([\d.]+)"', (ROOT / "scripts" / "lock_python.sh").read_text()).group(1)
    assert floor == "3.11"
    assert re.search(r'PY_MINOR"?\s*-lt\s*11', (ROOT / "install.sh").read_text())


# ── 점검기 ───────────────────────────────────────────────────────────────

def test_checker_passes_clean_tree(tmp_path):
    assert chk.check(_tree(tmp_path)) == []


def test_checker_flags_requirement_not_satisfied_by_lock(tmp_path):
    # requirements 를 올렸는데 잠금을 안 다시 만든 경우
    probs = chk.check(_tree(tmp_path, core_req="alpha>=2.0\n"))
    assert any("alpha" in p and "만족하지 않는다" in p for p in probs)


def test_checker_flags_requirement_missing_from_lock(tmp_path):
    probs = chk.check(_tree(tmp_path, core_req="alpha>=1.0\nnewpkg>=1\n"))
    assert any("newpkg" in p and "없다" in p for p in probs)


def test_checker_flags_missing_hash(tmp_path):
    probs = chk.check(_tree(tmp_path, core_lock="alpha==1.2 \\\n    # via -r requirements-core.txt\n"))
    assert any("해시가 없다" in p for p in probs)


def test_checker_flags_unpinned_or_url_lines(tmp_path):
    probs = chk.check(_tree(tmp_path, core_lock=f"alpha>=1.0 \\\n    {_H}\n"))
    assert any("고정이 아닌 줄" in p for p in probs)
    (tmp_path / "b").mkdir()
    probs = chk.check(_tree(tmp_path / "b", core_lock=f"alpha @ https://example.com/a.whl \\\n    {_H}\n"))
    assert any("고정이 아닌 줄" in p for p in probs)


def test_checker_flags_bad_hash_format(tmp_path):
    probs = chk.check(_tree(tmp_path, core_lock="alpha==1.2 \\\n    --hash=sha256:zz\n"))
    assert any("해시 형식" in p for p in probs)


def test_checker_flags_version_split_between_files(tmp_path):
    # voice 도 alpha 를 쓰는데 core 와 다른 버전 — 한 환경에 같이 깔면 충돌한다
    voice_lock = f"beta==2.1 \\\n    {_H}\nalpha==1.1 \\\n    {_H}\n"
    probs = chk.check(_tree(tmp_path, voice_lock=voice_lock))
    assert any("alpha" in p and "locks/core.txt" in p for p in probs)


def test_checker_flags_missing_lock_file(tmp_path):
    root = _tree(tmp_path)
    (root / "locks" / "voice.txt").unlink()
    assert any("locks/voice.txt 가 없다" in p for p in chk.check(root))


def test_checker_marker_pins_are_allowed(tmp_path):
    lock = (f"alpha==1.2 ; sys_platform == 'darwin' \\\n    {_H}\n"
            f"alpha==1.2 ; sys_platform != 'darwin' \\\n    {_H}\n")
    assert chk.check(_tree(tmp_path, core_lock=lock)) == []


def test_checker_cli_exit_code():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_python_lock.py")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


# ── 설치 경로는 잠금만 쓴다 ───────────────────────────────────────────────

def test_install_sh_installs_only_from_locks():
    s = (ROOT / "install.sh").read_text()
    assert "--require-hashes" in s
    assert not re.search(r'pip install[^\n]*requirements-(core|voice)\.txt', s), "install.sh 가 잠금 없이 requirements 로 설치한다"
    assert not re.search(r'pip install[^\n]*pyobjc', s), "pyobjc 는 잠금(voice)에 들어 있다"


def test_ci_installs_only_from_locks():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    for line in ci.splitlines():
        if "pip install" in line and not line.lstrip().startswith("#"):
            assert "--require-hashes" in line and "requirements-" not in line, line
    assert "check_python_lock.py" in ci


def test_install_sh_reports_missing_lock_and_hash_mismatch(tmp_path):
    """잠금 파일이 없을 때 알기 쉬운 오류를 낸다(셸 함수만 떼어 실행 — venv·pip 설치는 하지 않는다)."""
    src = (ROOT / "install.sh").read_text()
    body = src[src.index("_inst_pip_locked() {"):src.index('echo "▸ $(_t \'패키지 설치 중...\')"')]
    fake_pip = tmp_path / "bin" / "pip"
    fake_pip.parent.mkdir()
    fake_pip.write_text("#!/bin/bash\n"
                        "echo 'ERROR: THESE PACKAGES DO NOT MATCH THE HASHES FROM THE REQUIREMENTS FILE.' >&2\nexit 1\n")
    fake_pip.chmod(0o755)
    (tmp_path / "locks").mkdir()
    (tmp_path / "locks" / "core.txt").write_text("")
    script = ('_t() { local o="${1-}"; shift; for kv in "$@"; do o="${o//\\{${kv%%=*}\\}/${kv#*=}}"; done; printf "%s" "$o"; }\n'
              f'set -euo pipefail\nVT_DIR="{tmp_path}"\nPATH="{fake_pip.parent}:$PATH"\n{body}\n_inst_pip_locked "$@"\n')
    run = lambda *a: subprocess.run(["bash", "-c", script, "x", *a], capture_output=True, text=True)  # noqa: E731
    r = run("nope")
    assert r.returncode == 1 and "잠금 파일이 없습니다" in r.stdout
    r = run("core")
    assert r.returncode == 1 and "해시가 잠금 파일" in r.stdout and "끄고 설치하지는 마세요" in r.stdout


# ── fsh update ──────────────────────────────────────────────────────────

def _post_checkout(monkeypatch, tmp_path, changed, voice_installed):
    calls = []
    monkeypatch.setattr(updater, "git", lambda *a, **k: subprocess.CompletedProcess([], 0, "\n".join(changed), ""))
    monkeypatch.setattr(updater, "_python", lambda root: "PY")
    monkeypatch.setattr(updater.subprocess, "run",
                        lambda cmd, **k: subprocess.CompletedProcess(cmd, 0 if voice_installed else 1, "", ""))
    monkeypatch.setattr(updater, "_run", lambda cmd, cwd, step: calls.append(cmd))
    (tmp_path / "frontend" / "dist").mkdir(parents=True, exist_ok=True)
    updater.post_checkout(tmp_path, "a", "b")
    return calls


def test_updater_installs_from_hashed_locks(monkeypatch, tmp_path):
    calls = _post_checkout(monkeypatch, tmp_path, ["locks/core.txt"], voice_installed=False)
    assert calls == [["PY", "-m", "pip", "install", "--quiet", "--require-hashes", "-r", "locks/core.txt"]]


def test_updater_ignores_requirements_only_change(monkeypatch, tmp_path):
    assert _post_checkout(monkeypatch, tmp_path, ["requirements-core.txt"], voice_installed=False) == []


def test_updater_syncs_core_and_voice_together(monkeypatch, tmp_path):
    calls = _post_checkout(monkeypatch, tmp_path, ["locks/voice.txt"], voice_installed=True)
    assert calls == [["PY", "-m", "pip", "install", "--quiet", "--require-hashes",
                      "-r", "locks/core.txt", "-r", "locks/voice.txt"]]
    assert _post_checkout(monkeypatch, tmp_path, ["locks/voice.txt"], voice_installed=False) == []
