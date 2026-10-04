"""STT 엔진 공통 인터페이스.

엔진은 「모델 한 개를 올려 두고 16kHz mono WAV 바이트를 글자로 바꾸는 것」까지만 책임진다.
오디오 디코딩·무음 거름·길이 상한·직렬화(대기열)·타이밍 로그는 voice_handler가 한다.

규약:
- `transcribe`는 **블로킹**이다(호출자가 스레드에서 부른다). 동시 호출 보호는 호출자(voice_handler)가
  엔진 단위 락으로 한다 — 엔진 안에서 따로 직렬화할 필요 없다.
- 네트워크로 뭔가 받는 일(`install_requirement`, `download_model`)은 **사용자가 명시적으로 요청한 경로
  에서만** 불린다. `load`/`transcribe`는 절대 몰래 받지 않는다(없으면 NotReady).
- 진행 콜백은 `progress(done_bytes, total_bytes_or_None)`.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Callable, ClassVar, Optional

from .env import HostEnv

Progress = Callable[[int, Optional[int]], None]


class NotReady(RuntimeError):
    """엔진이나 모델이 준비되지 않았다 — 메시지는 그대로 사용자에게 보인다(한국어, 해야 할 일 포함)."""


@dataclass(frozen=True)
class Requirement:
    """엔진이 돌기 위해 필요한 것 하나(패키지·실행 파일·시스템 도구)."""
    id: str
    label: str
    kind: str                 # "pip" | "binary" | "system"
    satisfied: bool
    hint: str = ""            # 충족 못 했을 때 사용자가 할 일(설치 명령 등)
    can_install: bool = False  # 앱이 승인 후 대신 받아 줄 수 있는가(pip는 항상 False — 자동 설치 안 함)
    size_mb: Optional[int] = None

    def to_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "kind": self.kind, "satisfied": self.satisfied,
                "hint": self.hint, "can_install": self.can_install, "size_mb": self.size_mb}


class Engine(abc.ABC):
    id: ClassVar[str]
    label: ClassVar[str]

    # ── 환경 ───────────────────────────────────────────────────────
    @abc.abstractmethod
    def supported(self, env: HostEnv) -> tuple[bool, str]:
        """(이 호스트에서 쓸 수 있는가, 못 쓰면 이유 한 줄)."""

    @abc.abstractmethod
    def requirements(self, env: HostEnv) -> list[Requirement]: ...

    def install_requirement(self, req_id: str, progress: Progress) -> None:
        raise NotReady("이 항목은 앱이 대신 설치하지 않습니다 — 안내된 명령으로 직접 설치해 주세요")

    # ── 모델 ───────────────────────────────────────────────────────
    @abc.abstractmethod
    def model_present(self, spec) -> bool: ...

    @abc.abstractmethod
    def download_model(self, spec, progress: Progress) -> None: ...

    @abc.abstractmethod
    def delete_model(self, spec) -> int:
        """모델 파일을 지우고 회수한 바이트 수를 돌려준다(공유 파일은 남긴다)."""

    # ── 실행 ───────────────────────────────────────────────────────
    @abc.abstractmethod
    def load(self, spec) -> None:
        """모델을 올린다. 없으면 NotReady(몰래 받지 않는다)."""

    @abc.abstractmethod
    def unload(self) -> None:
        """올린 모델을 내리고 메모리를 돌려준다(자식 프로세스면 종료)."""

    @abc.abstractmethod
    def loaded(self) -> Optional[str]:
        """지금 올라가 있는 모델 id, 없으면 None."""

    @abc.abstractmethod
    def transcribe(self, wav: bytes, language: Optional[str]) -> str:
        """16kHz mono int16 WAV → 글자. 앞뒤 공백 제거. 블로킹."""

    def rss_mb(self) -> int:
        """이 엔진이 쓰는 메모리(MB) 어림값. 알 수 없으면 0."""
        return 0
