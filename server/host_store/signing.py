"""peer 요청 서명 · 재생 차단. **프로토콜 암호다 — 저장소와 변경 주기가 다르다.**

host_store에서 떼어낸 이유: 여기 있는 건 두 호스트가 **합의한 규칙**이라
한쪽만 바꾸면 통신이 끊긴다. grants/peers 목록을 어떻게 저장하느냐는 그
합의와 아무 상관이 없다. 한 파일에 두면 "이걸 고치면 상대 맥도 같이 고쳐야
하는가"가 안 보인다.

서명 대상에 method/path가 들어가는 것이 핵심이다 — view용 GET 서명을
control용 POST에 돌려쓸 수 없다. secret은 절대 전송되지 않는다(HMAC만 나간다).

**순수하다** — 파일도 설정도 안 읽는다. 시간창 상수만 인자로 흐른다.
"""

from __future__ import annotations

import hashlib
import hmac
import time

# 양쪽 시계 오차를 보정한 뒤의 기준. 넓히면 재생 창이 같이 넓어진다.
SIGNATURE_WINDOW_SEC = 60

def sign_request(secret: str, method: str, path: str, ts: int, nonce: str,
                 body_hash: str = "") -> str:
    """서명 대상에 method와 path를 포함한다 — 서명 하나를 다른 엔드포인트에
    돌려쓰지 못하게(예: view용 GET 서명을 control용 POST에 재사용) 막는다.

    A2(파일 전송)부터 **본문 해시**도 서명할 수 있다. 지금까지의 엔드포인트는
    본문이 작고 JSON이라 method+path만으로 충분했지만, 파일 바이트는 터널을
    지나가는 큰 덩어리라 "서명은 맞는데 내용이 바뀐" 경우를 구분할 수 있어야
    한다. `body_hash`가 빈 문자열이면 **서명 문자열이 예전과 글자 하나까지
    같다** — 구버전 상대와의 호환이 깨지지 않는다(그래서 조건부로 붙인다).
    """
    payload = f"{method.upper()}\n{path}\n{ts}\n{nonce}"
    if body_hash:
        payload += f"\n{body_hash}"
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def verify_signature(secret: str, method: str, path: str, ts: int, nonce: str,
                     sig: str, now: float | None = None, body_hash: str = "") -> bool:
    now = time.time() if now is None else now
    if abs(now - ts) > SIGNATURE_WINDOW_SEC:
        return False
    return hmac.compare_digest(
        sign_request(secret, method, path, ts, nonce, body_hash), sig or "")


class NonceCache:
    """재생 공격 차단 — 서명 창(60초) 안에서 같은 nonce를 두 번 받지 않는다.

    유효 시간창이 짧아 메모리 상한이 자연히 잡힌다(창 밖 항목은 어차피 서명
    검증에서 먼저 떨어지므로 지워도 안전). auth._Lockout의 _evict_stale과 같은 패턴.
    """

    def __init__(self, window: float = SIGNATURE_WINDOW_SEC):
        self._window = window
        self._seen: dict[str, float] = {}

    def check_and_add(self, key: str, now: float | None = None) -> bool:
        """처음 보는 nonce면 True(통과), 이미 본 것이면 False(거부)."""
        now = time.time() if now is None else now
        self._evict(now)
        if key in self._seen:
            return False
        self._seen[key] = now
        return True

    def _evict(self, now: float) -> None:
        cutoff = now - self._window * 2
        for k in [k for k, t in self._seen.items() if t < cutoff]:
            self._seen.pop(k, None)
