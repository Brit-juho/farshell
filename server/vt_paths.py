"""VT_* 경로 환경변수의 단일 해석기.

20곳이 `VT_STATE_DIR` 하나를 세 가지 관용구로 읽고 있었고, 빈 문자열·`~`·공백
패딩 셋 중 어디서든 갈라졌다. 해석을 여기로
모은다.

**이 모듈이 하지 않는 것**: mkdir · chmod · 마이그레이션 · flock. 그건 그
파일을 소유한 모듈의 일이다(이 모듈은 환경변수와 플랫폼 홈 판정만 맡는다). 여기는 순수 판정이라
I/O가 없고 테스트가 싸다(`fsguard`가 같은 이유로 같은 선택을 했다).

**왜 함수인가(상수가 아니라).** 전역에 굳히지 않고 호출 시점에 `os.environ`을
읽어야 테스트가 환경변수만으로 격리된다(`host_store/__init__.py`·
`worktree_ports.py`가 이미 같은 이유를 적어뒀다). 반대로 모듈 임포트 시점에
굳히면(예전 `auth/__init__.py`·`crypto_channel.py`) 같은 변수가 모듈마다
생사가 다르게 군다. **캐시도 하지 않는다** — `os.environ` 조회는 dict 조회라
쌀 것도 없고, 캐시하면 테스트 격리만 깨진다.

`VT_BROWSE_ROOTS`(코드 뷰어 열람 경계)는 여기 없다 — 경로 하나가 아니라
목록이고, 이미 `fsguard`가 루트 확정·해석·거부를 3층으로 소유한다. 경계
판정은 그쪽 일이다.
"""

from __future__ import annotations

import os
from pathlib import Path


class PathOverrideError(ValueError):
    """거부된 경로 override. 변수 이름·원본 값·이유를 모두 담는다.

    조용히 기본값으로 떨어지는 대신 **무엇이 왜 거부됐는지**를 말한다 —
    `fsh doctor`가 그대로 쓸 수 있는 형태다.
    """

    def __init__(self, variable: str, value: str, reason: str):
        self.variable = variable
        self.value = value
        self.reason = reason
        super().__init__(f'{variable}는 {reason} — 받은 값: "{value}"')


def _raw(name: str) -> str | None:
    """환경변수 원문. 빈 문자열·공백만이면 None(미설정으로 강등).

    실측 표(§1)의 `""` → CWD,
    `"   "` → 공백 이름 디렉터리 문제가 여기서 닫힌다.
    """
    v = os.environ.get(name)
    if v is None:
        return None
    v = v.strip()
    return v or None


def user_home() -> Path:
    """`HOME` 우선, 없으면 `Path.home()`(플랫폼 해석기)로 떨어진다.

    `Path.home()`은 환경에 따라 다르게 답할 수 있다 — `HOME`을 먼저 보고
    그다음 플랫폼 해석기로 떨어지는 순서를 고정해, CI와 tmux 안에서 같은
    답이 나오게 한다(결정적 순서, §2.5).

    `HOME`도 `Path.home()`도 실패하면 `PathOverrideError` — 어느 변수가
    `~`를 못 폈는지 이름을 싣는다(E7).
    """
    h = _raw("HOME")
    if h:
        return Path(h)
    try:
        return Path.home()
    except RuntimeError as e:
        raise PathOverrideError("HOME", "", "홈 디렉터리를 확인할 수 없다") from e


def _expand(name: str, raw: str) -> Path:
    """`~` 확장 + 절대경로 강제. 그 외 상대경로는 거절한다.

    프로세스가 저장소 안에서 돌 때
    전역 설정 override가 저장소-로컬 파일로 조용히 둔갑하는 사고를 막는다.
    """
    if raw == "~":
        return user_home()
    if raw.startswith("~/"):
        return user_home() / raw[2:]
    p = Path(raw)
    if not p.is_absolute():
        raise PathOverrideError(name, raw, "절대경로여야 한다")
    return p


def _resolve(name: str, default: Path) -> Path:
    raw = _raw(name)
    return default if raw is None else _expand(name, raw)


# --- 공개 API: 경로 하나당 함수 하나 -----------------------------------

def state_dir() -> Path:
    """VT_STATE_DIR. 기본 ~/.vt — 기기 목록·OTP·큐·스니펫 등이 사는 곳."""
    return _resolve("VT_STATE_DIR", user_home() / ".vt")


def run_dir() -> Path:
    """VT_RUN_DIR. 기본 `state_dir()/run` — pid·터널 로그 등 휘발성 런타임 파일(유닉스 소켓과 같은 폴더).

    2.7 전 기본값은 공용 `/tmp`였다. 고정 이름(`vt-pids/`, `cloudflared.log`)을 같은 맥의 다른 계정이
    먼저 만들어 두면 터널 주소·PID를 바꿔치기할 수 있었다. bin/fsh의 `_vt_run_default`와 같은 규칙이다.
    """
    return _resolve("VT_RUN_DIR", state_dir() / "run")


class UnsafeRunDir(RuntimeError):
    pass


def check_private_dir(path: Path) -> None:
    """런타임 폴더는 내 소유·심볼릭 링크 아님·0700 이어야 한다. 내 소유인데 느슨하면 0700으로 조이고,
    남의 것이거나 링크면 `UnsafeRunDir`. bin/fsh `_vt_private_dir`와 같은 판정(서버만 따로 띄운 경우용)."""
    try:
        st = path.lstat()
    except FileNotFoundError:
        return  # 아직 없다 — 만드는 쪽(bin/fsh)이 0700으로 만든다
    import stat as _stat
    if _stat.S_ISLNK(st.st_mode):
        raise UnsafeRunDir(f"{path} 가 심볼릭 링크다")
    if not _stat.S_ISDIR(st.st_mode):
        raise UnsafeRunDir(f"{path} 가 폴더가 아니다")
    if st.st_uid != os.getuid():
        raise UnsafeRunDir(f"{path} 의 소유자(uid {st.st_uid})가 내가 아니다")
    if st.st_mode & 0o077:
        os.chmod(path, 0o700)


def workspace_path() -> Path:
    """VT_WORKSPACE_PATH. 기본 ~/.config/vt/workspace.json."""
    return _resolve("VT_WORKSPACE_PATH", user_home() / ".config" / "vt" / "workspace.json")


def config_path() -> Path:
    """VT_CONFIG. 기본 ~/.vt.env.

    ⚠ `vt_env.py`의 부트스트랩 자체는 이 함수를 쓰지 않는다 — "어느 설정
    파일을 읽을지"를 결정하는 방식은 그 모듈이 계속 소유한다(모듈 docstring의
    범위 규율). `VT_CONFIG`는 설정 파일이 아니라 환경변수가 이겨야 하는
    유일한 경계값이기도 하다(`vt_env.py`의 `BOUNDARY_KEYS`가 이 키만 뺀
    이유 — "어느 파일을 읽을지 고르는 키라 env가 이겨야 한다"). 이 함수는
    "지금 설정 파일이 어디인지 알고 싶은" **다른** 호출부(진단 도구 등)를
    위한 것이다.
    """
    return _resolve("VT_CONFIG", user_home() / ".vt.env")


def detect_dir() -> Path:
    """VT_DETECT_DIR. 기본 <server>/detect — 에이전트 프롬프트 패턴 정의."""
    return _resolve("VT_DETECT_DIR", Path(__file__).parent / "detect")


def start_dir() -> Path:
    """VT_START_DIR. 기본 ~ — 새 셸/tmux 세션의 시작 디렉터리.

    디렉터리 존재 확인·미존재 시 홈 폴백은 `platform_utils.default_start_dir()`
    소유다(모듈 docstring의 범위 규율) — 여기는 순수 해석만 한다.
    """
    return _resolve("VT_START_DIR", user_home())


def install_dir() -> Path | None:
    """VT_DIR. 기본값이 없다 — 미설정이면 None.

    다른 함수와 달리 기본 경로로 떨어지지 않는다: fsh CLI가 어디 설치됐는지
    추측할 근거가 없다(E9). 호출부(`routes/ports.py`)는 None을 "CLI를
    찾을 수 없다"로 안내한다.
    """
    raw = _raw("VT_DIR")
    return None if raw is None else _expand("VT_DIR", raw)


def tunnel_log() -> Path:
    """run_dir()/cloudflared.log — 터널 로그의 단일 진실.

    §1(a) — `tunnel.py`·`tunnel_watchdog.py`가 `/tmp/cloudflared.log`를
    하드코딩해 `VT_RUN_DIR` 격리를 무시하고 있었다. 격리한 테스트 서버가
    실제 메인 터널의 로그를 읽는 사고로 이어졌다(E8).
    """
    return run_dir() / "cloudflared.log"


def is_isolated() -> bool:
    """VT_STATE_DIR 또는 VT_RUN_DIR이 명시적으로 설정됐는가.

    레거시 마이그레이션(`main.py`의 업로드 편입 등)이 이 값을 보고 건너뛴다
    — 격리를 선언한 프로세스는 레거시 편입을 하면 안 된다(§2.4, E12).
    """
    return _raw("VT_STATE_DIR") is not None or _raw("VT_RUN_DIR") is not None
