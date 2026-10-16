"""브라우저 어댑터 공통 계약(R9.1) — broker 는 이 인터페이스만 안다.

어댑터는 **격리된 프로필**(`profile = "isolated"`)의 탭만 다룬다. `profile = "user"`(사용자 Chrome 연결)는 이 빌드에서 구현하지 않는다 —
그렇게 선언한 어댑터는 `capabilities()` 가 `available=False`(`profile_user_unsupported`)로 보이고 broker 가 열기·켜기를 거부한다(후속 과제).

## 어댑터가 지키는 것
  - 조작은 broker 가 준 `ref`(어댑터 고유 탭 참조)로만 한다. 요소는 `snapshot()` 이 만든 어댑터 고유 `native` 로만 가리킨다 — 에이전트가 CSS/XPath/JS selector 를
    직접 넘기는 길이 없다(R9.4: JS 를 실행하는 selector 는 `browser.js`).
  - `open(url=, guard=)` 의 `guard(url, kind) -> Optional[str]` 은 **네트워크 계층**(요청 가로채기)에서 부른다: `kind` = `document`(최상위 문서·redirect hop)·
    `subframe`(서브프레임 문서)·`subresource`. 사유 문자열을 돌려주면 그 요청을 막는다. 가로채기를 못 하는 어댑터는 `intercepts = False` 로 두고 broker 가
    행동 뒤 `frame_urls()` 로 다시 검사한다.
  - 값 가림은 어댑터에서도 한다: `cookies(values=False)`·`storage(values=False)` 는 값을 아예 가져오지 않는다(broker 도 한 번 더 가린다).
  - 다운로드는 받지 않는다(거부). 새 창(popup)은 닫는다.
  - 브라우저가 사라졌으면 `AdapterDown` — broker 가 그 어댑터의 열린 탭을 `orphaned` 로 돌린다(F7). 그 밖의 실패는 `AdapterError`.
"""

from __future__ import annotations

from typing import Callable, Optional

OPS = ("open", "list", "navigate", "snapshot", "screenshot", "click", "type", "eval", "cookies", "storage", "network", "close")
PROFILES = ("isolated", "user")
Guard = Callable[[str, str], Optional[str]]


class AdapterError(Exception):
    """조작 실패(브라우저는 살아 있다). `code` 는 짧은 영문 코드."""

    def __init__(self, code: str, reason: str = ""):
        super().__init__(reason or code)
        self.code, self.reason = code, reason or code


class AdapterDown(AdapterError):
    """브라우저·어댑터에 닿지 못한다 — 열린 탭은 orphaned."""

    def __init__(self, reason: str = "adapter_down"):
        super().__init__("adapter_down", reason)


class Unavailable(AdapterError):
    def __init__(self, reason: str):
        super().__init__("adapter_unavailable", reason)


class BrowserAdapter:
    name: str = ""
    profile: str = "isolated"
    intercepts: bool = True              # 네트워크 계층에서 guard 를 거는가
    description: str = ""

    # --- 능력 ---------------------------------------------------------------------------------
    def capabilities(self) -> dict:
        """`{available, reason, ops, intercepts, version?}` — 이름·profile 은 registry 가 얹는다."""
        raise NotImplementedError

    def alive(self) -> bool:
        return True

    # --- 탭 -----------------------------------------------------------------------------------
    def open(self, *, url: Optional[str], guard: Guard) -> dict:
        """새 격리 탭 → `{"ref": str}`. url 이 있으면 열고 이동한다(첫 문서도 guard 를 지난다)."""
        raise NotImplementedError

    def list(self) -> list:
        """살아 있는 탭 `[{"ref", "url"}]` — broker 가 사라진 탭을 orphaned 로 돌리는 근거."""
        raise NotImplementedError

    def navigate(self, ref: str, url: str) -> dict:
        """`{"url", "title", "blocked": Optional[reason]}`"""
        raise NotImplementedError

    def snapshot(self, ref: str) -> dict:
        """`{"url", "title", "text", "elements": [{"ref": "e1", "role", "name", "native": <어댑터 고유>}]}`"""
        raise NotImplementedError

    def screenshot(self, ref: str) -> bytes:
        raise NotImplementedError

    def click(self, ref: str, native) -> None:
        raise NotImplementedError

    def type(self, ref: str, native, text: str, submit: bool = False) -> None:
        raise NotImplementedError

    def eval(self, ref: str, script: str):
        raise NotImplementedError

    def cookies(self, ref: str, values: bool) -> list:
        raise NotImplementedError

    def storage(self, ref: str, values: bool) -> dict:
        raise NotImplementedError

    def network(self, ref: str) -> list:
        raise NotImplementedError

    def frame_urls(self, ref: str) -> list:
        """최상위 문서와 모든 서브프레임의 현재 URL."""
        raise NotImplementedError

    def settle(self, ref: str) -> None:
        """행동 뒤 시작된 이동·로드가 가라앉기를 짧게 기다린다(유예 구간)."""

    def pop_blocked(self, ref: str) -> list:
        """마지막으로 비운 뒤 guard 가 막은 **문서·서브프레임** 로드의 사유들(하위 리소스는 세지 않는다)을 돌려주고 비운다 — 행동(클릭·입력·eval)이 일으킨 막힌 이동을 broker 가 알게 한다."""
        return []

    def blank(self, ref: str) -> None:
        """정책 위반 뒤 탭을 `about:blank` 로 돌린다(guard 를 거치지 않는다)."""
        raise NotImplementedError

    def close(self, ref: str) -> None:
        raise NotImplementedError

    def shutdown(self) -> None:
        """어댑터가 띄운 브라우저를 모두 닫는다."""


class StubAdapter(BrowserAdapter):
    """인터페이스만 만족하는 자리 표시 어댑터 — 항상 사용 불가이고 모든 조작을 거부한다. (`capabilities` 에 이유가 나온다.)"""

    stub_reason: str = "not_implemented"

    def capabilities(self) -> dict:
        return {"available": False, "reason": self.stub_reason, "ops": [], "intercepts": False, "stub": True}

    def alive(self) -> bool:
        return False

    def _no(self, *_a, **_k):
        raise Unavailable(self.stub_reason)

    open = list = navigate = snapshot = screenshot = click = type = eval = cookies = storage = network = frame_urls = blank = close = _no  # type: ignore[assignment]
