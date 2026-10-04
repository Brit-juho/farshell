"""준비(엔진 실행 파일·모델 받기) 작업 — 한 번에 하나, 백그라운드 스레드, 진행 상태 조회.

사용자가 확인 창에서 승인한 요청만 여기로 들어온다. 작업 하나는 **단계 하나**다(엔진 받기 또는 모델 받기);
여러 단계가 필요하면 UI가 차례로 요청한다. 끝난 작업은 다음 작업이 시작될 때까지 `status()`에 남아
(done/error) UI가 결과를 읽을 수 있다.
"""
from __future__ import annotations

import threading
import uuid
from typing import Callable, Optional

from .base import NotReady, Progress

_MAX_ERROR = 300


class SetupBusy(RuntimeError):
    pass


class SetupManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._job: Optional[dict] = None

    def status(self) -> Optional[dict]:
        with self._lock:
            return _copy(self._job)

    def start(self, model_id: str, step_id: str, label: str, fn: Callable[[Progress], None],
              blocking: bool = False) -> dict:
        """작업을 시작한다. 이미 진행 중이면 SetupBusy. `blocking`은 테스트용(스레드를 안 쓰고 끝까지 실행)."""
        with self._lock:
            if self._job is not None and self._job["state"] == "running":
                raise SetupBusy(f"이미 '{self._job['model']}' 준비 중입니다 — 끝난 뒤 다시 시도해 주세요")
            job = {"id": uuid.uuid4().hex[:8], "model": model_id, "state": "running", "error": None,
                   "steps": [{"id": step_id, "label": label, "state": "running", "done": 0, "total": None}]}
            self._job = job

        def progress(done: int, total: Optional[int]) -> None:
            with self._lock:
                if self._job is job:
                    s = job["steps"][0]
                    s["done"], s["total"] = int(done), (int(total) if total else None)

        def run() -> None:
            try:
                fn(progress)
            except NotReady as e:
                self._finish(job, str(e)[:_MAX_ERROR])
            except Exception as e:                        # 네트워크·디스크 등 — 메시지만 남긴다
                self._finish(job, f"{type(e).__name__}: {e}"[:_MAX_ERROR])
            else:
                self._finish(job, None)

        if blocking:
            run()
        else:
            threading.Thread(target=run, name=f"stt-setup-{step_id}", daemon=True).start()
        return self.status() or {}

    def _finish(self, job: dict, error: Optional[str]) -> None:
        with self._lock:
            step = job["steps"][0]
            step["state"] = "error" if error else "done"
            job["state"] = "error" if error else "done"
            job["error"] = error
            if not error and step["total"] is None:
                step["total"] = step["done"] or None


def _copy(job: Optional[dict]) -> Optional[dict]:
    if job is None:
        return None
    return {**job, "steps": [dict(s) for s in job["steps"]]}
