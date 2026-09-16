"""97번 3단계 — 자격증명을 tmux 세션의 프로세스 환경에 넣는다 (§2-4).

설정 파일에는 참조(`${VAR}`)만 심으므로, **누군가는 그 환경변수를 실제로
채워야** 한다. 안 채우면 에이전트가 "값이 비었다"며 실패한다. 그 채우는 일이
여기다.

## 왜 `tmux set-environment`에 값을 직접 넘기지 않는가

    tmux set-environment -t dev NOTION_TOKEN sk-진짜비밀키

이 명령줄은 실행되는 동안 `ps`로 **머신의 다른 계정에게 그대로 보인다.**
`~/.vt/mcp.json`은 0600이라 내 계정만 읽는데, 이 경로로 넣는 순간 그 경계가
넓어진다. 계획서 §2-1이 `fsh` CLI에 "stdin만 허용(argv 노출 금지)"을 건 것과
정확히 같은 이유다.

그래서 **값은 0600 파일에 쓰고, 명령줄에는 경로만 싣는다.** 경로는 비밀이
아니므로 `ps`에 보여도 무해하다. 세션은 그 파일을 읽어 환경을 채운다:

    set -a; . "<경로>"; set +a; claude

(`set -a`는 그 뒤 대입을 전부 export한다 — 자식 프로세스인 MCP 서버까지 닿아야
하므로 export가 필요하다.)

혼자 쓰는 맥이라면 어느 쪽이든 실질 차이가 없지만, 이 저장소는 남들도 설치하고
공용 장비·CI에서도 돌 수 있다. 기본값을 안전한 쪽에 두는 비용이 파일 하나뿐이라
그렇게 한다.

## 값은 어디까지 따라가는가

- 세션을 만들 때 그 스코프에서 **켜져 있는** 서버의 키만 쓴다(노출 최소).
- 그 뒤에 MCP를 켜면 파일을 다시 써서 **살아 있는 세션에도 따라간다** —
  다만 **이미 떠 있는 프로세스에는 안 닿는다.** 프로세스 환경은 뜬 뒤에
  바꿀 수 없기 때문이고, 이건 `mcp_scan.TOOL_FACTS`가 이미 말하고 있는
  "다음 세션부터 반영된다"와 같은 사실이다.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
from pathlib import Path

import mcp_catalog

logger = logging.getLogger(__name__)

# 세션 이름이 곧 파일 이름이 된다 — 경로 이탈(`../`)을 만들 수 있는 글자를
# 애초에 안 받는다. tmux 세션 이름에 `/`가 들어갈 수 있으므로 실제 위험이다.
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")

ENV_POINTER = "FSH_MCP_ENV_FILE"   # 세션에 심는 **경로** 변수(비밀 아님)


def _run_dir() -> Path:
    base = os.environ.get("VT_RUN_DIR") or os.environ.get("VT_STATE_DIR") or "~/.vt"
    return Path(base).expanduser() / "mcp-env"


def env_file_for(session: str) -> Path:
    safe = _SAFE_NAME.sub("-", session or "").strip("-") or "session"
    return _run_dir() / f"{safe[:80]}.env"


def _format(env: dict[str, str]) -> str:
    """`KEY='값'` 줄들. **셸이 읽을 파일이므로 따옴표 처리가 곧 방어다** —
    값에 `;`나 `$(…)`가 들어 있으면 그대로 실행돼 버린다. shlex.quote가
    그걸 막는다(작은따옴표로 감싸고 내부 작은따옴표를 이스케이프한다)."""
    lines = []
    for key in sorted(env):
        # 이름은 mcp_catalog.normalize_env_name을 통과한 것만 들어온다.
        lines.append(f"{key}={shlex.quote(env[key])}")
    return "\n".join(lines) + ("\n" if lines else "")


def write_env_file(session: str, env: dict[str, str]) -> Path | None:
    """세션용 0600 환경 파일을 쓴다. 넣을 게 없으면 파일을 **지운다**
    (남겨두면 나중에 MCP를 껐는데도 키가 계속 주입된다)."""
    path = env_file_for(session)
    if not env:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.warning(f"MCP env 파일 삭제 실패({e})")
        return None

    d = path.parent
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(_format(env))
    os.replace(str(tmp), str(path))
    return path


def env_for_scope(worktree_id: str | None = None) -> dict[str, str]:
    """이 워크트리에서 **켜져 있는** 서버에 필요한 환경변수 묶음.

    꺼져 있는 서버의 키는 넣지 않는다 — 노출 범위를 실제로 쓰는 것으로
    한정한다는 §2-1의 뜻 그대로다. 지문이 어긋난 서버도 `mcp_catalog.env_for`가
    걸러낸다(§2-5).
    """
    import mcp_scan

    try:
        scan = mcp_scan.scan(worktree_id)
    except Exception:
        logger.exception("MCP scan 실패 — 환경 주입을 건너뛴다")
        return {}

    enabled = []
    for s in scan.get("servers", []):
        if not s.get("enabled"):
            continue
        enabled.append({"name": s["name"], "fingerprint": s.get("fingerprint")})
    return mcp_catalog.env_for(enabled)


def prepare(session: str, worktree_id: str | None = None) -> Path | None:
    """세션 하나 몫의 환경 파일을 준비한다. 경로(또는 넣을 게 없으면 None)."""
    return write_env_file(session, env_for_scope(worktree_id))


def source_prefix(path: Path | None) -> str:
    """에이전트 명령 앞에 붙일 조각. 값이 아니라 **경로만** 명령줄에 실린다.

    `set -a`로 감싸는 이유: MCP 서버는 CLI의 **자식 프로세스**로 뜨므로
    export되지 않은 셸 변수로는 닿지 않는다.
    """
    if path is None:
        return ""
    q = shlex.quote(str(path))
    return f"set -a; [ -f {q} ] && . {q}; set +a; "
