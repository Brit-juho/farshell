"""Qwen3-ASR (MLX) — 애플 칩 맥 전용. `mlx-qwen3-asr` 패키지(torch 불필요)로 돌린다.

모델 가중치는 HuggingFace(`mlx-community/Qwen3-ASR-*-8bit`)에서 받는다. 8bit만 쓴다 — 4bit는 한국어에서
오류가 크게 늘어난다는 보고가 있다(soniqo 벤치마크: Fleurs 한국어 WER 4bit 19.95% vs 8bit 6.89%).
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import threading
from typing import Optional

from . import hfcache, pins
from .base import Engine, NotReady, Progress, Requirement
from .env import HostEnv
from i18n import tr

_PKG = "mlx_qwen3_asr"
_LANG_NAMES = {"ko": "Korean", "en": "English", "ja": "Japanese", "zh": "Chinese"}


class MlxQwen3Engine(Engine):
    id = "mlx_qwen3"
    label = "MLX (Qwen3-ASR)"

    def __init__(self) -> None:
        self._model_id: Optional[str] = None
        self._model_path: Optional[str] = None
        self._transcribe_fn = None
        self._lock = threading.Lock()

    # ── 환경 ───────────────────────────────────────────────────────
    def supported(self, env: HostEnv) -> tuple:
        if env.os == "macos" and env.arch == "arm64":
            return True, ""
        return False, tr("애플 칩(M1~M5) 맥에서만 쓸 수 있습니다 (MLX는 애플 GPU 전용)")

    def requirements(self, env: HostEnv) -> list:
        ok = importlib.util.find_spec(_PKG) is not None
        return [Requirement(
            id="mlx-qwen3-asr", label=tr("mlx-qwen3-asr 패키지"), kind="pip", satisfied=ok,
            hint="" if ok else f"{sys.executable} -m pip install mlx-qwen3-asr",
            can_install=False)]

    # ── 모델 ───────────────────────────────────────────────────────
    def _snapshot(self, spec, verify: bool = False) -> Optional[str]:
        if pins.pin_for(spec.repo) is not None:
            # 카탈로그 모델은 고정된 파일 목록·해시와 맞는 것만 쓴다(verify=True면 해시까지 — 쓰기 직전).
            snap = pins.verified_snapshot(spec.repo) if verify else pins.complete_snapshot(spec.repo)
            return str(snap) if snap is not None else None
        snap = hfcache.cached_snapshot(spec.repo)
        # config.json이 있으면 받기가 끝난 스냅샷으로 본다(받는 도중에는 snapshots/가 비어 있다).
        if snap is not None and (snap / "config.json").exists():
            return str(snap)
        return None

    def model_present(self, spec) -> bool:
        return self._snapshot(spec) is not None

    def download_model(self, spec, progress: Progress) -> None:
        from ._progress import poll_cache_progress
        total = (spec.size_mb or 0) * 1_000_000 or None   # 카탈로그 용량은 십진 MB
        with poll_cache_progress(spec.repo, total, progress):
            if pins.pin_for(spec.repo) is not None:
                pins.download_pinned(spec.repo)           # 커밋 고정 + SHA-256 검증
            else:                                         # 직접 입력한 모델 — 고정 못 함
                from huggingface_hub import snapshot_download
                snapshot_download(spec.repo)

    def delete_model(self, spec) -> int:
        if self._model_id == spec.id:
            self.unload()
        return hfcache.delete_repo(spec.repo)

    # ── 실행 ───────────────────────────────────────────────────────
    def load(self, spec) -> None:
        with self._lock:
            if self._model_id == spec.id and self._transcribe_fn is not None:
                return
            if importlib.util.find_spec(_PKG) is None:
                raise NotReady(tr("mlx-qwen3-asr 패키지가 설치돼 있지 않습니다 — 설정 「음성 및 알림」의 안내대로 설치해 주세요"))
            path = self._snapshot(spec, verify=True)     # PinError(NotReady)면 해시 불일치
            if path is None:
                raise NotReady(tr("음성 인식 모델 '{model}'을(를) 아직 받지 않았습니다 — 설정 「음성 및 알림」에서 먼저 받아 주세요", model=spec.label))
            from mlx_qwen3_asr import transcribe as _t      # 무거운 import는 여기서
            self._transcribe_fn, self._model_path, self._model_id = _t, path, spec.id

    def unload(self) -> None:
        with self._lock:
            self._transcribe_fn = self._model_path = self._model_id = None
            try:
                import gc
                gc.collect()
                import mlx.core as mx
                mx.clear_cache()           # MLX가 쥔 GPU/통합 메모리를 돌려준다
            except Exception:
                pass

    def loaded(self) -> Optional[str]:
        return self._model_id if self._transcribe_fn is not None else None

    def transcribe(self, wav: bytes, language: Optional[str]) -> str:
        fn, path = self._transcribe_fn, self._model_path
        if fn is None:
            raise NotReady(tr("음성 인식 모델이 올라와 있지 않습니다"))
        lang = _LANG_NAMES.get((language or "").lower()) if language else None
        fd, tmp = tempfile.mkstemp(suffix=".wav", prefix="fsh-stt-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(wav)
            return fn(tmp, model=path, language=lang).text.strip()
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def rss_mb(self) -> int:
        try:
            import resource
            return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024))
        except Exception:
            return 0
