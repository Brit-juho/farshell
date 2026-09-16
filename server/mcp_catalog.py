"""97번 2~3단계 — MCP 카탈로그. 그룹 태그 + 자격증명을 담는다.

파일은 `~/.vt/mcp.json` 하나이고 최상위가 객체다:

    {"version": 1, "tags": {…}, "creds": {…}, "refs": {…}}

- `tags`  — 그룹 태그(2단계). 서버 **이름**에 붙는다.
- `creds` — 자격증명(3단계). **평문 0600.** 아래 「왜 평문인가」 참고.
- `refs`  — 우리가 어느 CLI 파일에 어떤 참조를 써넣었는지의 기록(3단계).
            이름 규칙과 **무관하게** 일괄 회수할 수 있게 하는 유일한 근거다.

## 왜 평문인가 (§2-1)

암호화하지 않고 키체인도 쓰지 않는다. 복호화 키를 같은 프로세스가 읽어야 하니
키 파일도 같은 계정 아래에 있고, 그 계정을 얻은 공격자는 둘 다 읽는다. OS
키체인이 진짜 방어가 되는 건 **잠금 해제를 물어볼 때뿐**인데, 서버가 무인으로
돌아야 해서 미리 허가할 수밖에 없고 — 물어보게 두면 **폰에서 원격 조작 중인
사용자가 맥 앞에 없어 누를 수 없다.** 이 제품의 핵심 용도와 정면 충돌한다.

**무인 동작과 저장 상태 보호는 동시에 성립하지 않는다.** 그래서 실제 방어는
전부 §2-2에 있다 — **CLI 설정 파일에는 값을 절대 쓰지 않고 참조만 쓴다.**
그것만이 `.mcp.json`이 git에 커밋되며 키가 저장소에 박히는 사고를 막는다.

암호화한다고 포장하지 않는다. "GitHub 토큰과 같은 수준"이라고 정직하게 말한다
(실제로 `git_account_store.py`가 같은 디렉토리에 같은 방식으로 이미 있다).

## 왜 우리 쪽에 저장하는가 — 그리고 무엇을 저장하지 *않는가*

그룹은 **각 CLI 설정 파일에 전혀 기록되지 않는다**(§1-3). FarShell 화면 안에만
존재하는 묶음이다. claude·codex·agy 어느 쪽에도 "그룹"이라는 개념이 없으므로,
거기 써넣으면 그 도구가 모르는 키를 남기는 셈이고 도구가 설정을 다시 쓸 때
조용히 사라진다.

**on/off 상태는 여기 저장하지 않는다.** 그건 매번 CLI 파일에서 읽는다
(`mcp_scan`). 우리가 상태를 복제하면 "우리 기록과 실제 파일이 어긋난다"는
문제가 생기는데, 사용자가 터미널에서 직접 고치는 게 정상인 이 제품에서는
반드시 어긋난다.

## 태그가 붙는 단위는 "이름"이다

같은 이름의 MCP 서버가 도구·스코프마다 따로 나타난다(claude 전역의 `github`와
codex 전역의 `github`는 별개 항목이다). 태그를 항목마다 따로 붙이면 사용자가
같은 서버를 세 번 태그해야 하고, 한 곳만 태그가 빠진 채로 "그룹 켜기"가
그 항목을 건너뛴다 — 그게 바로 §1-3이 없애려던 종류의 사고다. 그래서 태그는
**이름 하나**에 붙고, 그룹 조작은 그 이름을 가진 **모든 항목**에 적용된다.
`mcp_scan.group_by_name()`이 이미 같은 단위로 묶고 있다.

저장은 `~/.vt/mcp.json` — 0700 디렉토리 + 0600 파일 + flock + 임시파일 rename.
이 저장소의 기존 저장 파일(snippet_store·queue_store)과 동일한 규율이다.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import re
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

# 태그는 화면의 칩 하나다. 길면 칩이 줄을 먹고, 많으면 고르는 화면이 목록이
# 된다 — 그룹은 "몇 개를 한 번에"가 목적이지 분류 체계가 아니다.
MAX_TAG_LEN = 24
MAX_TAGS_TOTAL = 30        # 서로 다른 태그 이름의 총 개수
MAX_TAGS_PER_NAME = 8
MAX_NAME_LEN = 200

# 환경변수 이름으로 쓸 수 있는 문자만. 값이 아니라 **이름**을 검사하는 것이고,
# 이게 곧 남의 설정 파일에 써넣을 문자열이므로 여기서 조인다.
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MAX_ENV_NAME_LEN = 96
MAX_SECRET_LEN = 8192

# 제어문자·경로 구분자를 막는다. 태그는 파일 경로가 되지 않지만, 화면과 로그를
# 오염시키는 값(개행·탭)을 애초에 안 받는 게 싸다.
_TAG_BAD = re.compile(r"[\x00-\x1f\x7f/\\]")


def _state_dir() -> Path:
    return Path(os.environ.get("VT_STATE_DIR", "~/.vt")).expanduser()


def _path() -> Path:
    return _state_dir() / "mcp.json"


def _lock_path() -> Path:
    return _state_dir() / "mcp.lock"


@contextmanager
def _locked():
    d = _state_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    fd = os.open(str(_lock_path()), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _read_unlocked() -> dict:
    p = _path()
    if not p.is_file():
        return {"version": 1, "tags": {}, "creds": {}, "refs": {}}
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        # 스니펫 저장소와 같은 판단: 읽기 실패로 서버를 세우지 않는다. 태그가
        # 사라지는 건 아프지만 복구 가능하고, 여기서 예외를 올리면 MCP 화면
        # 전체가 안 뜬다.
        logger.warning(f"MCP 카탈로그 읽기 실패({e}) — 빈 카탈로그로 시작")
        return {"version": 1, "tags": {}, "creds": {}, "refs": {}}
    if not isinstance(data, dict):
        return {"version": 1, "tags": {}, "creds": {}, "refs": {}}

    raw = data.get("tags")
    tags: dict[str, list[str]] = {}
    if isinstance(raw, dict):
        for name, vals in raw.items():
            if not isinstance(name, str) or not isinstance(vals, list):
                continue
            clean = _clean_tags(vals)
            if clean:
                tags[name] = clean
    data["tags"] = tags
    data["creds"] = data.get("creds") if isinstance(data.get("creds"), dict) else {}
    data["refs"] = data.get("refs") if isinstance(data.get("refs"), dict) else {}
    data.setdefault("version", 1)
    return data


def _write_unlocked(data: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p.parent, 0o700)
    except OSError:
        pass
    tmp = p.with_name(p.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(str(tmp), str(p))


def normalize_tag(tag: object) -> str | None:
    """받아들일 수 있는 태그면 정규화해 돌려주고, 아니면 None.

    대소문자는 **구분하지 않는다** — 화면에서 「검증용」과 「검증용 」이 다른
    그룹으로 갈라지면 사용자는 왜 그룹 켜기가 일부를 빼먹는지 알 수 없다.
    """
    if not isinstance(tag, str):
        return None
    t = tag.strip()
    if not t or len(t) > MAX_TAG_LEN or _TAG_BAD.search(t):
        return None
    return t


def _clean_tags(vals: list) -> list[str]:
    """중복을 없애고 순서를 지킨다(사용자가 붙인 순서가 칩 순서가 된다)."""
    out: list[str] = []
    seen: set[str] = set()
    for v in vals:
        t = normalize_tag(v)
        if t is None:
            continue
        key = t.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
        if len(out) >= MAX_TAGS_PER_NAME:
            break
    return out


# --- 공개 API ---------------------------------------------------------------


def get_tags() -> dict[str, list[str]]:
    """{서버 이름: [태그…]}. 태그가 없는 이름은 아예 들어 있지 않다."""
    with _locked():
        return _read_unlocked()["tags"]


def all_tags() -> list[str]:
    """지금 쓰이고 있는 태그 이름 전부(칩 목록용). casefold 기준 중복 제거."""
    seen: dict[str, str] = {}
    for vals in get_tags().values():
        for t in vals:
            seen.setdefault(t.casefold(), t)
    return [seen[k] for k in sorted(seen)]


def set_tags(name: str, tags: list) -> dict:
    """서버 이름 하나의 태그를 **통째로 교체**한다(추가/삭제 API를 따로 두지
    않는다 — 화면이 칩 목록 전체를 들고 있으므로 교체가 더 단순하고, 두 탭에서
    동시에 고쳐도 마지막 쓰기가 온전한 목록이 된다).
    """
    if not isinstance(name, str) or not name.strip() or len(name) > MAX_NAME_LEN:
        return {"ok": False, "error": "invalid_name"}
    name = name.strip()
    if not isinstance(tags, list):
        return {"ok": False, "error": "invalid_tags"}

    clean = _clean_tags(tags)
    with _locked():
        data = _read_unlocked()
        current = data["tags"]

        # 새로 생기는 태그가 총량 상한을 넘기는지. 이미 있는 태그를 다른
        # 서버에 더 붙이는 건 총량을 늘리지 않으므로 세지 않는다.
        existing = {t.casefold() for n, vals in current.items() if n != name for t in vals}
        kept = {t.casefold() for t in clean}
        if len(existing | kept) > MAX_TAGS_TOTAL:
            return {"ok": False, "error": "too_many_tags",
                    "reason": f"태그는 최대 {MAX_TAGS_TOTAL}종류까지입니다"}

        if clean:
            current[name] = clean
        else:
            current.pop(name, None)   # 빈 목록은 "태그 없음" — 키를 남기지 않는다
        _write_unlocked(data)
    return {"ok": True, "name": name, "tags": clean}


def rename_tag(old: str, new: str) -> dict:
    """태그 이름을 모든 서버에서 한 번에 바꾼다.

    이게 없으면 오타 하나를 고치려고 서버를 하나씩 다시 태그해야 하고, 그동안
    그룹이 둘로 갈라진 채 "그룹 켜기"가 절반만 켠다.
    """
    o, n = normalize_tag(old), normalize_tag(new)
    if o is None or n is None:
        return {"ok": False, "error": "invalid_tag"}
    changed = 0
    with _locked():
        data = _read_unlocked()
        for name, vals in list(data["tags"].items()):
            if not any(v.casefold() == o.casefold() for v in vals):
                continue
            replaced = [n if v.casefold() == o.casefold() else v for v in vals]
            data["tags"][name] = _clean_tags(replaced)   # 새 이름이 이미 있으면 합쳐진다
            changed += 1
        if changed:
            _write_unlocked(data)
    return {"ok": True, "renamed": changed, "tag": n}


def delete_tag(tag: str) -> dict:
    """태그를 모든 서버에서 뗀다. 서버 자체는 건드리지 않는다."""
    t = normalize_tag(tag)
    if t is None:
        return {"ok": False, "error": "invalid_tag"}
    changed = 0
    with _locked():
        data = _read_unlocked()
        for name, vals in list(data["tags"].items()):
            rest = [v for v in vals if v.casefold() != t.casefold()]
            if len(rest) == len(vals):
                continue
            changed += 1
            if rest:
                data["tags"][name] = rest
            else:
                data["tags"].pop(name, None)
        if changed:
            _write_unlocked(data)
    return {"ok": True, "removed": changed, "tag": t}


def members(tag: str, tags_map: dict[str, list[str]] | None = None) -> list[str]:
    """그 태그가 붙은 서버 **이름** 목록."""
    t = normalize_tag(tag)
    if t is None:
        return []
    m = tags_map if tags_map is not None else get_tags()
    return sorted(
        name for name, vals in m.items()
        if any(v.casefold() == t.casefold() for v in vals)
    )


# --- 자격증명 (3단계) --------------------------------------------------------
#
# 값은 여기 평문으로 있고(파일 머리말 「왜 평문인가」), **API로 내려가는 모든
# 경로는 cred_public()을 거친다.** git_account_store.py와 같은 규칙이다.


def auto_env_name(server: str, key: str) -> str:
    """`FSH_MCP_<서버>_<키>` — 우리가 제안하는 기본 환경변수 이름.

    자동 생성을 기본값으로 두는 이유(2026-09-16 사용자 확정): 사용자가 이름을
    직접 정하면 우리가 심은 참조와 원래 셸에 있던 참조를 구분할 수 없어
    **일괄 회수(§2-5)가 불가능**해진다. 다만 이름을 강제하지는 않는다 —
    이미 `.zshrc`에 `NOTION_TOKEN`을 쓰던 사람이 그 이름을 그대로 쓰고 싶은
    건 정당하다. 그래서 회수 추적은 이름 규칙이 아니라 `refs` 기록에 건다.
    """
    def clean(x: str) -> str:
        return re.sub(r"[^A-Za-z0-9]+", "_", (x or "")).strip("_").upper() or "X"
    return f"FSH_MCP_{clean(server)}_{clean(key)}"[:MAX_ENV_NAME_LEN]


def normalize_env_name(name: object) -> str | None:
    """환경변수로 쓸 수 있는 이름인지. 이 문자열이 곧 **남의 설정 파일에
    써넣을 값**이므로 여기서 조인다 — 공백·따옴표·`$`가 들어가면 그 파일의
    문법을 깨뜨리거나 더 나쁜 것을 만든다."""
    if not isinstance(name, str):
        return None
    n = name.strip()
    if not n or len(n) > MAX_ENV_NAME_LEN or not _ENV_NAME.match(n):
        return None
    return n


def fingerprint(defn: dict) -> str:
    """서버 정의의 지문 — "이름이 아니라 검증된 대상에 묶는다"(§2-5).

    같은 이름이지만 실행 명령이나 URL이 바뀐 서버에 키가 자동으로 흘러가면
    안 된다. 이름은 사용자가 아무렇게나 붙이는 라벨이고, 실제로 그 키를 받는
    건 **그 명령/URL로 뜨는 프로세스**다.
    """
    import hashlib
    parts = [
        str(defn.get("command") or ""),
        " ".join(str(x) for x in (defn.get("args") or [])),
        str(defn.get("url") or ""),
    ]
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:16]


def mask_secret(secret: str) -> str:
    """`sk-1…9f2a` 형태. 클라이언트로 내려가는 **유일한** 형태여야 한다."""
    if not secret:
        return ""
    if len(secret) <= 8:
        return "…" + secret[-2:]
    return f"{secret[:4]}…{secret[-4:]}"


def cred_public(c: dict) -> dict:
    """API 응답용 — 원문을 제외하고 마스킹만."""
    return {
        "id": c.get("id"),
        "server": c.get("server"),
        "key": c.get("key"),
        "env": c.get("env"),
        "masked": mask_secret(c.get("secret") or ""),
        "fingerprint": c.get("fingerprint"),
        "created": c.get("created"),
    }


def _cred_id(server: str, key: str) -> str:
    """(서버, 키) 하나당 자격증명 하나 — id를 그 둘에서 결정적으로 만든다.
    같은 칸에 두 번 넣으면 새 항목이 쌓이는 게 아니라 **갱신**이 되어야 한다."""
    slug = re.sub(r"[^a-z0-9]+", "-", f"{server}-{key}".lower()).strip("-")
    return slug[:80] or "cred"


def set_cred(server: str, key: str, secret: str, *,
             env: str | None = None, fingerprint_: str | None = None) -> dict:
    """자격증명 하나를 넣거나 갱신한다. 응답에 원문은 절대 안 담는다."""
    if not isinstance(server, str) or not server.strip():
        return {"ok": False, "error": "invalid_server"}
    if not isinstance(key, str) or not key.strip():
        return {"ok": False, "error": "invalid_key"}
    if not isinstance(secret, str) or not secret:
        return {"ok": False, "error": "invalid_secret"}
    if len(secret) > MAX_SECRET_LEN:
        return {"ok": False, "error": "secret_too_long"}

    server, key = server.strip(), key.strip()
    # 빈 문자열은 "지정 안 함"이다 — 화면의 빈 입력칸이 그대로 올라오므로,
    # 여기서 거절하면 사용자가 기본 이름을 쓰려고 칸을 비운 것이 오류가 된다.
    name = normalize_env_name(env) if (env or "").strip() else auto_env_name(server, key)
    if name is None:
        return {"ok": False, "error": "invalid_env_name",
                "reason": "환경변수 이름은 영문자·숫자·밑줄만 쓸 수 있습니다"}

    cid = _cred_id(server, key)
    with _locked():
        data = _read_unlocked()
        prev = data["creds"].get(cid) or {}
        data["creds"][cid] = {
            "id": cid, "server": server, "key": key, "env": name,
            "secret": secret,
            "fingerprint": fingerprint_ or prev.get("fingerprint"),
            "created": prev.get("created") or int(__import__("time").time()),
        }
        _write_unlocked(data)
        return {"ok": True, "cred": cred_public(data["creds"][cid])}


def list_creds() -> list[dict]:
    """**원문 포함**(서버 내부용). API 응답 직전엔 반드시 cred_public을 거칠 것."""
    with _locked():
        return list(_read_unlocked()["creds"].values())


def public_creds() -> list[dict]:
    return [cred_public(c) for c in sorted(list_creds(), key=lambda x: (x["server"], x["key"]))]


def delete_cred(cred_id: str) -> dict:
    with _locked():
        data = _read_unlocked()
        if data["creds"].pop(cred_id, None) is None:
            return {"ok": False, "error": "not_found"}
        _write_unlocked(data)
    return {"ok": True, "id": cred_id}


def env_for(servers: list[dict]) -> dict[str, str]:
    """이 서버들에 필요한 환경변수 묶음 — tmux 세션에 넣을 값.

    **지문이 어긋나면 넣지 않는다**(§2-5). 같은 이름으로 다른 명령이 걸려 있는
    서버에 키가 자동으로 흘러가는 것을 막는 지점이고, 여기가 유일한 관문이다.
    지문이 아예 기록되지 않은(구형) 자격증명은 대상을 특정한 적이 없다는 뜻이라
    이름으로만 맞춘다 — 그 사실은 `unbound`로 함께 돌려준다.
    """
    by_server: dict[str, list[dict]] = {}
    for c in list_creds():
        by_server.setdefault(c["server"], []).append(c)

    out: dict[str, str] = {}
    for s in servers:
        for c in by_server.get(s.get("name"), []):
            fp = c.get("fingerprint")
            if fp and fp != s.get("fingerprint"):
                continue        # 대상이 바뀌었다 — 자동 주입 거부
            out[c["env"]] = c["secret"]
    return out


# --- 참조 기록 (회수용) ------------------------------------------------------


def record_ref(*, tool: str, scope: str, source: str, server: str, env: str) -> None:
    """우리가 어느 CLI 파일에 어떤 참조를 써넣었는지 남긴다.

    §2-5의 "키 회수 경로"가 이것 하나에 달려 있다. 이름 규칙으로 추적하면
    사용자가 이름을 덮어쓴 순간 추적이 끊기므로, **쓴 사실 자체**를 기록한다.
    """
    rid = f"{tool}:{scope}:{source}:{server}:{env}"
    with _locked():
        data = _read_unlocked()
        data["refs"][rid] = {
            "tool": tool, "scope": scope, "source": source,
            "server": server, "env": env,
            "written": int(__import__("time").time()),
        }
        _write_unlocked(data)


def list_refs() -> list[dict]:
    with _locked():
        return sorted(_read_unlocked()["refs"].values(),
                      key=lambda r: (r["server"], r["tool"], r["source"]))


def forget_ref(tool: str, scope: str, source: str, server: str, env: str) -> None:
    rid = f"{tool}:{scope}:{source}:{server}:{env}"
    with _locked():
        data = _read_unlocked()
        if data["refs"].pop(rid, None) is not None:
            _write_unlocked(data)
