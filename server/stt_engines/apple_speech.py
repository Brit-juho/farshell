"""Apple SpeechAnalyzer 엔진 (macOS 26+, ko-KR, 온디바이스).

Swift 헬퍼(apple_speech.swift)를 상주 프로세스(--serve)로 띄워 stdin 경로 → stdout JSON 줄로 받아쓴다.

- 헬퍼 실행파일은 **승인 후 빌드**한다(`install_requirement`). 소스 sha256 앞 8자리로 이름을 지어
  소스가 바뀌면 자동으로 다시 빌드 대상이 된다. `load`는 절대 몰래 빌드하지 않는다.
- 「모델」은 OS가 관리하는 ko-KR 언어 자산이다. 앱이 지울 수 없으므로 `delete_model`은 0을 돌려준다.
"""
from __future__ import annotations

import atexit
import hashlib
import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Optional

import vt_paths

from .base import Engine, NotReady, Progress, Requirement
from .env import HostEnv

SRC = Path(__file__).with_suffix(".swift")
MODEL_ID = "apple-speech-ko"
HELPER_REQ = "apple-helper"
_REPLY_TIMEOUT = 30.0     # 헬퍼 응답 대기 상한(초)
_CHECK_TIMEOUT = 30.0
_BUILD_TIMEOUT = 600.0
_SETUP_MSG = "설정에서 먼저 준비해 주세요"


def _src_hash() -> str:
    return hashlib.sha256(SRC.read_bytes()).hexdigest()[:8]


def helper_path() -> Path:
    """현재 소스에 해당하는 헬퍼 경로(없을 수도 있다)."""
    return vt_paths.state_dir() / "engines" / "apple" / f"stt_apple-{_src_hash()}"


def _helper_ready() -> bool:
    p = helper_path()
    return p.is_file() and os.access(p, os.X_OK)


def check_path_line(path: str) -> str:
    """헬퍼는 한 줄에 경로 하나를 받는다 — 개행이 섞이면 프로토콜이 깨지므로 거부한다."""
    if "\n" in path or "\r" in path:
        raise ValueError("경로에 개행 문자를 쓸 수 없습니다")
    return path


class AppleSpeechEngine(Engine):
    id = "apple_speech"
    label = "Apple 음성 인식 (SpeechAnalyzer)"

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._lines: Optional[queue.Queue] = None
        self._lock = threading.Lock()
        atexit.register(self._kill)

    # ── 환경 ───────────────────────────────────────────────────────
    def supported(self, env: HostEnv) -> tuple[bool, str]:
        if env.os == "macos" and env.arch == "arm64" and env.macos_version and tuple(env.macos_version) >= (26, 0):
            return True, ""
        return False, "macOS 26 이상의 애플 칩 맥에서만 쓸 수 있습니다"

    def requirements(self, env: HostEnv) -> list[Requirement]:
        has_swiftc = shutil.which("swiftc") is not None
        return [
            Requirement(id="swiftc", label="Swift 컴파일러 (swiftc)", kind="system", satisfied=has_swiftc,
                        hint="" if has_swiftc else "xcode-select --install"),
            Requirement(id=HELPER_REQ, label="음성 인식 헬퍼 (앱이 직접 빌드)", kind="binary",
                        satisfied=_helper_ready(),
                        hint="" if _helper_ready() else "설정에서 「받기」를 누르면 빌드합니다",
                        can_install=has_swiftc),
        ]

    def install_requirement(self, req_id: str, progress: Progress) -> None:
        if req_id != HELPER_REQ:
            return super().install_requirement(req_id, progress)
        if not shutil.which("swiftc"):
            raise NotReady("swiftc가 없습니다 — 터미널에서 `xcode-select --install`을 실행해 주세요")
        if _helper_ready():
            return
        dest = helper_path()
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + f".tmp{os.getpid()}")
        progress(0, None)
        try:
            r = subprocess.run(["swiftc", "-parse-as-library", "-O", str(SRC), "-o", str(tmp)],
                               capture_output=True, text=True, timeout=_BUILD_TIMEOUT)
            if r.returncode != 0:
                tail = (r.stderr.strip().splitlines() or ["알 수 없는 오류"])[-1]
                raise NotReady(f"헬퍼 빌드에 실패했습니다: {tail}")
            os.chmod(tmp, 0o755)
            os.replace(tmp, dest)
        except subprocess.TimeoutExpired:
            raise NotReady("헬퍼 빌드가 시간 안에 끝나지 않았습니다")
        finally:
            tmp.unlink(missing_ok=True)
        for old in dest.parent.glob("stt_apple-*"):      # 옛 소스 해시 빌드 정리
            if old != dest and ".tmp" not in old.name:
                old.unlink(missing_ok=True)
        progress(1, 1)

    # ── 모델(= 시스템 언어 자산) ───────────────────────────────────
    def model_present(self, spec) -> bool:
        if not _helper_ready():
            return False
        try:
            r = subprocess.run([str(helper_path()), "--check"], capture_output=True, text=True,
                               timeout=_CHECK_TIMEOUT)
            return r.returncode == 0 and json.loads(r.stdout.strip().splitlines()[-1]).get("installed") == "1"
        except (subprocess.SubprocessError, OSError, ValueError, IndexError):
            return False

    def download_model(self, spec, progress: Progress) -> None:
        if not _helper_ready():
            raise NotReady(f"음성 인식 헬퍼가 아직 없습니다. {_SETUP_MSG}")
        progress(0, None)
        p = subprocess.Popen([str(helper_path()), "--install"], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
        err_box: list = []
        t = threading.Thread(target=lambda: err_box.append(p.stderr.read()), daemon=True)
        t.start()
        done = False
        try:
            for line in p.stdout:
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if "progress" in o:
                    tot = int(o.get("total") or 0)
                    progress(int(o["progress"]), tot if tot > 0 else None)
                elif o.get("installed") == "1":
                    done = True
            p.wait(timeout=_BUILD_TIMEOUT)
        except subprocess.TimeoutExpired:
            p.kill()
            raise NotReady("한국어 음성 자산 설치가 시간 안에 끝나지 않았습니다")
        finally:
            if p.poll() is None:
                p.kill()
            t.join(timeout=2)
        if p.returncode != 0 or not done:
            tail = ((err_box[0] if err_box else "").strip().splitlines() or ["알 수 없는 오류"])[-1]
            raise NotReady(f"한국어 음성 자산 설치에 실패했습니다: {tail}")
        progress(1, 1)

    def delete_model(self, spec) -> int:
        # 시스템(OS)이 관리하는 언어 자산이라 앱이 지울 수 없다 — 아무것도 지우지 않는다.
        return 0

    # ── 실행 ───────────────────────────────────────────────────────
    def load(self, spec) -> None:
        with self._lock:
            if self._alive():
                return
            if not _helper_ready():
                raise NotReady(f"음성 인식 헬퍼가 아직 없습니다. {_SETUP_MSG}")
            p = subprocess.Popen([str(helper_path()), "--serve"], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
            q: queue.Queue = queue.Queue()

            def pump():
                for ln in p.stdout:
                    q.put(ln)
                q.put(None)
            threading.Thread(target=pump, daemon=True).start()
            err_box: list = []
            threading.Thread(target=lambda: err_box.append(p.stderr.read()), daemon=True).start()
            self._proc, self._lines = p, q
            try:
                first = q.get(timeout=_REPLY_TIMEOUT)
            except queue.Empty:
                self._kill()
                raise NotReady("음성 인식 헬퍼가 시간 안에 준비되지 않았습니다")
            if not first or json.loads(first).get("ready") != "1":
                code = p.wait(timeout=5) if p.poll() is None else p.returncode
                self._kill()
                if code == 3:
                    raise NotReady(f"한국어 음성 자산이 설치되지 않았습니다. {_SETUP_MSG}")
                raise NotReady(f"음성 인식 헬퍼를 시작하지 못했습니다 (종료 코드 {code})")

    def unload(self) -> None:
        with self._lock:
            self._kill()

    def loaded(self) -> Optional[str]:
        return "apple-speech-ko" if self._alive() else None

    def transcribe(self, wav: bytes, language: Optional[str]) -> str:
        # language는 쓰지 않는다 — 헬퍼는 ko-KR 고정이다.
        with self._lock:
            if not self._alive():
                raise NotReady(f"음성 인식 헬퍼가 실행 중이 아닙니다. {_SETUP_MSG}")
            fd, path = tempfile.mkstemp(suffix=".wav", prefix="apple-stt-")
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(wav)
                check_path_line(path)
                self._proc.stdin.write(path + "\n")
                self._proc.stdin.flush()
                try:
                    line = self._lines.get(timeout=_REPLY_TIMEOUT)
                except queue.Empty:
                    self._kill()
                    raise RuntimeError("음성 인식 헬퍼가 시간 안에 응답하지 않아 종료했습니다")
                if not line:
                    self._kill()
                    raise RuntimeError("음성 인식 헬퍼가 예기치 않게 종료되었습니다")
                o = json.loads(line)
                if "error" in o:
                    raise RuntimeError(o["error"])
                return o["text"].strip()
            except (BrokenPipeError, OSError) as e:
                if isinstance(e, FileNotFoundError):
                    raise
                self._kill()
                raise RuntimeError(f"음성 인식 헬퍼와 통신하지 못했습니다: {e}")
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass

    def rss_mb(self) -> int:
        if not self._alive():
            return 0
        try:
            out = subprocess.run(["ps", "-o", "rss=", "-p", str(self._proc.pid)], capture_output=True,
                                 text=True, timeout=5).stdout.strip()
        except (subprocess.SubprocessError, OSError):
            return 0
        return int(out) // 1024 if out.isdigit() else 0

    # ── 내부 ───────────────────────────────────────────────────────
    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _kill(self) -> None:
        """stdin 닫기 → 대기 → 강제 종료. 이미 없으면 아무것도 안 한다."""
        p, self._proc, self._lines = self._proc, None, None
        if p is None:
            return
        try:
            if p.poll() is None:
                try:
                    p.stdin.close()
                except OSError:
                    pass
                try:
                    p.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait(timeout=3)
        except Exception:
            pass
