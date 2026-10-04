"""다운로드 진행률 어림 — HF 캐시 폴더 크기를 주기적으로 읽어 콜백한다.

huggingface_hub의 tqdm 훅은 버전·전송 방식(xet)에 따라 달라서 믿기 어렵다. 폴더 크기는 어떤 방식이든
같이 늘어나므로 이 값을 쓴다. 받는 중인 파일은 `blobs/*.incomplete` 이거나 xet 캐시일 수 있어 정확한 바이트가
아니라 **진행 표시용 어림값**이다.
"""
from __future__ import annotations

import contextlib
import threading
from typing import Optional

from . import hfcache
from .base import Progress


@contextlib.contextmanager
def poll_cache_progress(repo: str, total: Optional[int], progress: Progress, interval: float = 0.5):
    stop = threading.Event()

    def _run():
        while not stop.is_set():
            try:
                progress(hfcache.dir_size(hfcache.repo_dir(repo)), total)
            except Exception:
                pass
            stop.wait(interval)

    t = threading.Thread(target=_run, name="stt-progress", daemon=True)
    t.start()
    try:
        yield
    finally:
        stop.set()
        t.join(timeout=2)
        try:
            size = hfcache.dir_size(hfcache.repo_dir(repo))
            progress(size, total or size)
        except Exception:
            pass
