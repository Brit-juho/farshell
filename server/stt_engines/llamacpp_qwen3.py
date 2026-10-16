"""Qwen3-ASR (llama.cpp GGUF) — 모든 플랫폼. `llama-server`를 **자식 프로세스**로 띄워 HTTP로 부른다.

왜 별도 프로세스인가: (1) 모델을 내리면 프로세스가 끝나 메모리가 OS로 **실제로** 돌아간다(예전 CTranslate2는
네이티브 메모리가 안 돌아갔다), (2) 엔진이 죽어도 FarShell 서버가 같이 죽지 않는다.

보안: 127.0.0.1에만 바인딩, 포트는 OS가 정한 빈 포트, 실행마다 임의 API 키를 건다 — 같은 머신의 다른
프로세스가 이 포트를 두드려도 키 없이는 못 쓴다. 터널로 노출되지 않는다.
GPU는 쓰지 않는다(`-ngl 0`) — 실측으로 검증한 구성(CPU 전용)만 쓴다. llama.cpp의 오디오 입력은 아직 실험 단계다.
"""
from __future__ import annotations

import atexit
import base64
import json
import os
import secrets
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from . import hfcache, llama_release
from .base import Engine, NotReady, Progress, Requirement
from .env import HostEnv
from i18n import tr

_START_TIMEOUT = 180       # 큰 모델 첫 로드 여유
_REQUEST_TIMEOUT = 90
_MAX_TOKENS = 1024


def parse_asr_text(content: str) -> str:
    """응답은 `language Korean<asr_text>본문` 형태 — 본문만 쓴다. 태그가 없으면 전체를 본문으로 본다."""
    return (content or "").split("<asr_text>", 1)[-1].strip()


class LlamaCppQwen3Engine(Engine):
    id = "llamacpp_qwen3"
    label = "llama.cpp (Qwen3-ASR)"

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._model_id: Optional[str] = None
        self._base: str = ""
        self._key: str = ""
        self._lock = threading.Lock()
        atexit.register(self.unload)

    # ── 환경 ───────────────────────────────────────────────────────
    def supported(self, env: HostEnv) -> tuple:
        if llama_release.asset_for(env) is not None or llama_release.find_binary() is not None:
            return True, ""
        return False, tr("이 환경({env})용 llama.cpp 공식 배포본이 없습니다 — 직접 설치한 뒤 환경변수 VT_LLAMA_SERVER에 경로를 지정하면 쓸 수 있습니다", env=env.label)

    def requirements(self, env: HostEnv) -> list:
        found = llama_release.find_binary()
        asset = llama_release.asset_for(env)
        reqs = self._base_requirements(env, found, asset)
        if env.os == "linux":
            missing = _linux_missing_libs(found)
            reqs.append(Requirement(
                id="linux-libs", label=tr("시스템 라이브러리 (OpenMP: libgomp)"), kind="system",
                satisfied=not missing,
                hint="" if not missing else
                     (tr("sudo apt install libgomp1  (Debian/Ubuntu) · sudo dnf install libgomp  (Fedora) 로 설치해 주세요")
                      if missing == ["libgomp"] else
                      tr("sudo apt install libgomp1  (Debian/Ubuntu) · sudo dnf install libgomp  (Fedora) 로 설치해 주세요 — 없는 라이브러리: {libs}",
                         libs=", ".join(missing)))))
        return reqs

    def _base_requirements(self, env: HostEnv, found, asset) -> list:
        return [Requirement(
            id="llama-server", label=tr("llama.cpp (llama-server 실행 파일)"), kind="binary",
            satisfied=found is not None,
            hint="" if found else tr("설정에서 받거나, 직접 설치한 뒤 VT_LLAMA_SERVER에 경로를 지정하세요"),
            can_install=asset is not None,
            size_mb=round(asset[2] / 1024 / 1024) if asset else None)]

    def install_requirement(self, req_id: str, progress: Progress) -> None:
        if req_id != "llama-server":
            raise NotReady(tr("알 수 없는 항목: {req_id}", req_id=req_id))
        from .env import detect
        try:
            llama_release.download_and_install(detect(), progress)
        except llama_release.ReleaseError as e:
            raise NotReady(str(e)) from e

    # ── 모델 ───────────────────────────────────────────────────────
    def _files(self, spec) -> Optional[tuple]:
        """(모델 gguf, mmproj gguf) 캐시 경로. 하나라도 없으면 None."""
        names = spec.files or _guess_files(spec.repo)
        if not names:
            return None
        paths = [hfcache.cached_file(spec.repo, n) for n in names]
        if any(p is None for p in paths):
            return None
        model = next((p for p, n in zip(paths, names) if not n.startswith("mmproj")), None)
        mmproj = next((p for p, n in zip(paths, names) if n.startswith("mmproj")), None)
        return (model, mmproj) if model and mmproj else None

    def model_present(self, spec) -> bool:
        return self._files(spec) is not None

    def download_model(self, spec, progress: Progress) -> None:
        from huggingface_hub import snapshot_download
        from ._progress import poll_cache_progress
        patterns = list(spec.files) or _repo_gguf_patterns(spec.repo)
        total = (spec.size_mb or 0) * 1_000_000 or None   # 카탈로그 용량은 십진 MB
        with poll_cache_progress(spec.repo, total, progress):
            snapshot_download(spec.repo, allow_patterns=patterns)

    def delete_model(self, spec) -> int:
        if self._model_id == spec.id:
            self.unload()
        names = list(spec.files) or list(_guess_files(spec.repo) or ())
        return hfcache.delete_repo_files(spec.repo, names) if names else 0

    # ── 실행 ───────────────────────────────────────────────────────
    def load(self, spec) -> None:
        with self._lock:
            if self._model_id == spec.id and self._proc is not None and self._proc.poll() is None:
                return
            # 서버가 SIGKILL 등으로 죽어 남은 옛 프로세스(pid 파일)를 먼저 정리한다 — 우리 프로세스를 멈추면
            # pid 파일이 지워져 그 뒤에는 찾을 수 없다. 지금 우리 프로세스가 살아 있으면 pid 파일은 그 것이라 건드리지 않는다.
            if self._proc is None or self._proc.poll() is not None:
                self._kill_orphan()
            self._stop_locked()
            binary = llama_release.find_binary()
            if binary is None:
                raise NotReady(tr("llama.cpp(llama-server)가 없습니다 — 설정 「음성 및 알림」에서 먼저 받아 주세요"))
            files = self._files(spec)
            if files is None:
                raise NotReady(tr("음성 인식 모델 '{model}'을(를) 아직 받지 않았습니다 — 설정 「음성 및 알림」에서 먼저 받아 주세요", model=spec.label))
            port = _free_port()
            key = secrets.token_urlsafe(24)
            log = _log_path()
            log.parent.mkdir(parents=True, exist_ok=True)
            cmd = [str(binary), "-m", str(files[0]), "--mmproj", str(files[1]),
                   "-ngl", "0", "--no-mmproj-offload", "-c", "2048", "-np", "1", "--no-webui",
                   "--host", "127.0.0.1", "--port", str(port), "--api-key", key]
            logf = open(log, "wb")
            try:
                proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                        start_new_session=True)
            finally:
                logf.close()
            self._proc, self._base, self._key = proc, f"http://127.0.0.1:{port}", key
            _pid_path().write_text(f"{proc.pid}\n{binary}")     # 고아 정리 때 우리 프로세스인지 대조하는 용도
            try:
                self._wait_ready(proc, log)
            except Exception:
                self._stop_locked()
                raise
            self._model_id = spec.id

    def _wait_ready(self, proc: subprocess.Popen, log: Path) -> None:
        deadline = time.monotonic() + _START_TIMEOUT
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise NotReady(tr("llama.cpp 엔진이 시작 중 종료됐습니다: {log}", log=_log_tail(log)))
            try:
                req = urllib.request.Request(self._base + "/health")
                with urllib.request.urlopen(req, timeout=2) as r:
                    if json.load(r).get("status") == "ok":
                        return
            except Exception:
                pass
            time.sleep(0.4)
        raise NotReady(tr("llama.cpp 엔진 준비 시간이 초과됐습니다: {log}", log=_log_tail(log)))

    def _stop_locked(self) -> None:
        proc, self._proc, self._model_id = self._proc, None, None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except Exception:
                    pass
        try:
            _pid_path().unlink()
        except OSError:
            pass

    def unload(self) -> None:
        with self._lock:
            self._stop_locked()

    def loaded(self) -> Optional[str]:
        p = self._proc
        return self._model_id if p is not None and p.poll() is None else None

    def transcribe(self, wav: bytes, language: Optional[str]) -> str:
        if self.loaded() is None:
            raise NotReady(tr("음성 인식 엔진이 올라와 있지 않습니다"))
        body = {"messages": [{"role": "user", "content": [
            {"type": "input_audio", "input_audio": {"data": base64.b64encode(wav).decode(), "format": "wav"}}]}],
            "temperature": 0, "max_tokens": _MAX_TOKENS}
        req = urllib.request.Request(
            self._base + "/v1/chat/completions", data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self._key}"})
        try:
            with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as r:
                content = json.load(r)["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            raise RuntimeError(tr("llama.cpp 엔진 오류 {code}: {body}", code=e.code, body=repr(e.read()[:200]))) from e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if self._proc is not None and self._proc.poll() is not None:
                self.unload()
                raise NotReady(tr("llama.cpp 엔진이 종료돼 있습니다 — 다시 시도하면 새로 시작합니다")) from e
            raise RuntimeError(tr("llama.cpp 엔진 응답 실패: {e}", e=e)) from e
        return parse_asr_text(content)

    def rss_mb(self) -> int:
        p = self._proc
        if p is None or p.poll() is not None:
            return 0
        from ._proc import rss_mb
        return rss_mb(p.pid)

    def _kill_orphan(self) -> None:
        """서버가 SIGKILL 등으로 죽어 남은 옛 llama-server(우리가 띄운 것)를 정리한다 — 메모리를 안 쥐게."""
        try:
            lines = _pid_path().read_text().splitlines()
            pid = int(lines[0].strip())
            binary = lines[1].strip() if len(lines) > 1 else ""
        except (OSError, ValueError, IndexError):
            return
        try:
            # -ww: 폭 제한을 푼다. 리눅스 procps는 COLUMNS가 걸린 환경에서 파이프로 읽어도 명령행을 그 폭에서 잘라, 경로가
            # 긴 설치에서는 뒤쪽의 --api-key가 사라져 우리 프로세스를 못 알아보고 고아를 그대로 남겼다.
            cmd = subprocess.run(["ps", "-ww", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout
        except OSError:
            return
        # 우리가 띄운 것인지 확인: 실행 파일 경로(기록돼 있으면)와 우리가 거는 --api-key가 모두 명령행에 있어야 한다.
        # pid가 재사용돼 사용자의 다른 llama-server가 된 경우를 건드리지 않는다.
        if "llama-server" in cmd and "--api-key" in cmd and (not binary or binary in cmd):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        try:
            _pid_path().unlink()
        except OSError:
            pass


# ── 도우미 ─────────────────────────────────────────────────────────

def _linux_missing_libs(binary: Optional[Path]) -> list:
    """리눅스에서 llama-server가 로드에 필요한데 없는 공유 라이브러리 이름들. 최소 이미지에는 OpenMP(libgomp)가
    없어 엔진이 시작 중 종료된다(실측: python:slim 컨테이너). 실행 파일이 있으면 ldd로, 없으면 libgomp만 확인한다."""
    import ctypes.util
    import shutil
    if binary is not None and shutil.which("ldd"):
        try:
            out = subprocess.run(["ldd", str(binary)], capture_output=True, text=True, timeout=10).stdout
            names = [l.split("=>")[0].strip() for l in out.splitlines() if "not found" in l]
            if names:
                return [n.split(".so")[0] for n in names]
            return []
        except (OSError, subprocess.SubprocessError):
            pass
    return [] if ctypes.util.find_library("gomp") else ["libgomp"]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _state_engines_dir() -> Path:
    import vt_paths
    return vt_paths.state_dir() / "engines"


def _log_path() -> Path:
    return _state_engines_dir() / "llama-server.log"


def _pid_path() -> Path:
    return _state_engines_dir() / "llama-server.pid"


def _log_tail(log: Path, n: int = 3) -> str:
    try:
        lines = [l for l in log.read_text(errors="replace").splitlines() if l.strip()]
        return " | ".join(lines[-n:])[:400]
    except OSError:
        return tr("(로그 없음)")


def _repo_gguf_patterns(repo: str) -> list:
    """직접 입력한 GGUF 리포에서 받을 파일 패턴 — Q8_0 모델 + 같은 양자화(없으면 첫) mmproj."""
    from huggingface_hub import list_repo_files
    return _pick_gguf(list_repo_files(repo))


def _pick_gguf(names: list) -> list:
    ggufs = [n for n in names if n.endswith(".gguf")]
    models = [n for n in ggufs if not n.startswith("mmproj")]
    projs = [n for n in ggufs if n.startswith("mmproj")]
    if not models or not projs:
        raise NotReady(tr("이 리포에는 llama.cpp용 모델(.gguf)과 mmproj 파일이 함께 없습니다"))
    model = next((n for n in models if "Q8_0" in n), models[0])
    proj = next((n for n in projs if "Q8_0" in n), projs[0])
    return [model, proj]


def _guess_files(repo: str) -> Optional[tuple]:
    """캐시에 이미 받아 둔 직접 입력 리포의 파일 이름(스냅샷을 훑어 같은 규칙으로 고른다)."""
    snap = hfcache.cached_snapshot(repo)
    if snap is None:
        return None
    names = [p.name for p in snap.iterdir() if p.name.endswith(".gguf")]
    try:
        return tuple(_pick_gguf(names))
    except NotReady:
        return None
