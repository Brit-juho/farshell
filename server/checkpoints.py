"""에이전트 턴 체크포인트 — 작업 폴더 상태를 사용자 브랜치가 아닌 숨은 ref에 git 객체로 남긴다.

설계: docs/local의 t3code 레퍼런스 설계안 2.1(구조·캡처 절차는 t3code가 쓰는 방식과 같다). 이 모듈은 **동기 핵심 로직만**
있다 — 스레드·`ckpt` 풀에서 부른다(이벤트 루프에서 직접 부르지 않는다: `test_no_blocking_in_async.py`).
훅·라우트·UI 연결은 이 모듈에 없다(다음 단계). 이 모듈만으로는 아무것도 자동 실행되지 않는다.

## 사용자 저장소를 바꾸지 않는다

캡처는 임시 인덱스(`<git-dir>/fsh-ckpt-index-<uuid>`)에서 `add -A → write-tree → commit-tree → update-ref`만 한다.
사용자의 브랜치·HEAD·인덱스(스테이징)·작업 폴더는 읽기만 한다. 바뀌는 것은 `.git/objects`와 `refs/fsh/ckpt/v1/…`뿐이다.
ref 이름: `refs/fsh/ckpt/v1/<wt12>/<sess16>/<seq:06d>`, 복원 안전판 `refs/fsh/ckpt/v1/<wt12>/restore/<UTC초>`.
`wt12` = `sha1(실경로 absolute-git-dir)[:12]` — linked worktree끼리 `refs/`를 공유하므로 작업 폴더별로 나눈다.
**ref가 유일한 영구 저장소**다(FarShell 상태 폴더에는 아무것도 쓰지 않는다).

## 안전

- 모든 git 호출은 `git_safe.argv`(훅·fsmonitor 끔)와 `resilience.proc.run`(기한 지나면 프로세스 그룹째 종료)을 거친다.
- 비밀값: `fsguard.repo_relpath_denied`에 걸리는 경로(.env·*.pem·id_rsa·.ssh/·허용 밖의 점 디렉터리)는 캡처에서 뺀다.
  규칙을 새로 만들지 않고 열람 규칙과 같은 함수를 쓴다. diff는 `routes.git._redact_diff`를 거친다.
- 커밋 메시지·로그에는 경로·지시문·tmux 이름을 넣지 않는다(사용자의 말은 이 맥 화면에만 나가는 값이다).
- `git gc`/`prune`은 부르지 않는다. 객체 정리는 사용자 git의 몫이다.
- 호출자가 `top`을 `fsguard.resolve_under_roots`로 이미 검증했다고 가정한다(이 모듈은 경로 경계를 판정하지 않는다).
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

import fsguard
import git_safe
from resilience import proc as res_proc
from resilience.locks import LockTimeout, TimedLock

logger = logging.getLogger(__name__)

REF_PREFIX = "refs/fsh/ckpt/v1/"
KINDS = ("baseline", "pre-turn", "turn-end", "pre-restore")
_SESS_RE = re.compile(r"^[0-9a-f]{16}$")
_AGENT_RE = re.compile(r"^[a-z0-9_-]{1,16}$")
# 복원·diff가 받는 id: `<wt12>/<sess16>/<seq>` 또는 `<wt12>/restore/<UTC초>` (접두 `v1/` 포함).
ID_RE = re.compile(r"^v1/[0-9a-f]{12}/(?:[0-9a-f]{16}/\d{6}|restore/\d{10})$")

_IDENT = {
    "GIT_AUTHOR_NAME": "FarShell checkpoint", "GIT_AUTHOR_EMAIL": "checkpoint@farshell.invalid",
    "GIT_COMMITTER_NAME": "FarShell checkpoint", "GIT_COMMITTER_EMAIL": "checkpoint@farshell.invalid",
}
# t3code와 같은 이유: 파일 상태 캐시·split index·fsmonitor가 임시 인덱스 계산을 흔들지 않게 한다.
_INDEX_CFG = ("-c", "core.splitIndex=false", "-c", "core.fsmonitor=false")
_STALE_INDEX_SECONDS = 3600


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    raw = os.environ.get(name, "").strip()
    if raw:
        try:
            n = int(raw)
            if lo <= n <= hi:
                return n
        except ValueError:
            pass
        logger.warning("%s=%r 무시 — %d~%d 정수만 받는다", name, raw, lo, hi)
    return default


def timeout_seconds() -> int:
    return _env_int("VT_CKPT_TIMEOUT", 30, 1, 600)


def max_file_bytes() -> int:
    # 2 MB(측정 결과 — 큰 추적 안 된 파일이 매 캡처 비용의 대부분이었다). 코드·설정은 거의 다 담고 바이너리·데이터는 건너뛴다.
    return _env_int("VT_CKPT_MAX_FILE_MB", 2, 1, 1024) * 1024 * 1024


def max_total_bytes() -> int:
    return _env_int("VT_CKPT_MAX_BYTES_MB", 100, 1, 10240) * 1024 * 1024


def max_paths() -> int:
    return _env_int("VT_CKPT_MAX_PATHS", 5000, 1, 1_000_000)


def keep_turns() -> int:
    return _env_int("VT_CKPT_KEEP_TURNS", 30, 1, 10000)


def keep_days() -> int:
    return _env_int("VT_CKPT_KEEP_DAYS", 14, 1, 3650)


def keep_restore_days() -> int:
    return _env_int("VT_CKPT_KEEP_RESTORE_DAYS", 7, 1, 3650)


def max_refs() -> int:
    return _env_int("VT_CKPT_MAX_REFS", 500, 10, 1_000_000)


# --- 내부 도구 -----------------------------------------------------------------


class _Timeout(Exception):
    pass


class _GitError(Exception):
    def __init__(self, what: str, rc: int):
        super().__init__(f"git {what} rc={rc}")
        self.what = what
        self.rc = rc


class _Deadline:
    def __init__(self, seconds: float):
        self.end = time.monotonic() + seconds

    def left(self) -> float:
        r = self.end - time.monotonic()
        if r <= 0:
            raise _Timeout()
        return r


def _git(top: Path, deadline: _Deadline, *args: str, env: Optional[dict] = None, input: Optional[bytes] = None,
         check: bool = True, readable_paths: bool = False) -> subprocess.CompletedProcess:
    argv = git_safe.argv(*args, cwd=top, readable_paths=readable_paths)
    try:
        cp = res_proc.run(argv, timeout=deadline.left(), env=git_safe.env(env), input=input)
    except subprocess.TimeoutExpired:
        raise _Timeout() from None
    if check and cp.returncode != 0:
        raise _GitError(" ".join(a for a in args if not a.startswith("-"))[:40], cp.returncode)
    return cp


def _text(b: bytes) -> str:
    return b.decode("utf-8", errors="replace").strip()


_locks: dict[str, TimedLock] = {}
_locks_guard = threading.Lock()


def _lock_for(git_dir: str) -> TimedLock:
    with _locks_guard:
        lk = _locks.get(git_dir)
        if lk is None:
            lk = _locks[git_dir] = TimedLock(f"ckpt:{git_dir[-24:]}")
        return lk


LOCK_WAIT_SECONDS = 3.0


class _Repo:
    """한 저장소(작업 폴더)의 읽기 전용 사실."""

    def __init__(self, top: Path, toplevel: Path, git_dir: str, head: str):
        self.top = top                      # git에 넘기는 cwd(작업 트리 루트)
        self.toplevel = toplevel
        self.git_dir = git_dir
        self.head = head                    # "" = 첫 커밋 전
        self.wt12 = hashlib.sha1(git_dir.encode("utf-8", "surrogateescape")).hexdigest()[:12]


def _open(top: Path, deadline: _Deadline) -> Optional[_Repo]:
    cp = _git(top, deadline, "rev-parse", "--show-toplevel", "--absolute-git-dir", check=False)
    if cp.returncode != 0:
        return None
    lines = _text(cp.stdout).splitlines()
    if len(lines) != 2:
        return None
    toplevel = Path(lines[0])
    git_dir = os.path.realpath(lines[1])
    head = _text(_git(toplevel, deadline, "rev-parse", "-q", "--verify", "HEAD", check=False).stdout)
    if not re.fullmatch(r"[0-9a-f]{40,64}", head or ""):
        head = ""
    return _Repo(toplevel, toplevel, git_dir, head)


def _busy_state(git_dir: str) -> bool:
    return any(os.path.exists(os.path.join(git_dir, n)) for n in
               ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply"))


def _config_bool(repo: _Repo, deadline: _Deadline, key: str) -> bool:
    cp = _git(repo.top, deadline, "config", "--type=bool", "--get", key, check=False)
    return _text(cp.stdout) == "true"


def _clean_stale_indexes(git_dir: str) -> None:
    """저장소별 첫 캡처 때: 1시간 넘은 임시 인덱스(프로세스가 죽어 못 지운 것)를 지운다."""
    now = time.time()
    try:
        names = os.listdir(git_dir)
    except OSError:
        return
    for n in names:
        if not n.startswith("fsh-ckpt-index-"):
            continue
        p = os.path.join(git_dir, n)
        try:
            if now - os.lstat(p).st_mtime > _STALE_INDEX_SECONDS:
                os.unlink(p)
        except OSError:
            pass


_cleaned: set[str] = set()


def _split_nul(raw: bytes) -> list[bytes]:
    return [p for p in raw.split(b"\0") if p]


def _fs(path: bytes) -> str:
    return os.fsdecode(path)


def _result(status: str, **kw) -> dict:
    out = {"status": status, "ref": None, "id": None, "seq": None, "tree": None, "commit": None,
           "skipped": 0, "paths": 0, "reason": ""}
    out.update(kw)
    return out


def _parse_meta(message: str) -> dict:
    meta: dict = {}
    for line in message.splitlines():
        m = re.match(r"^FarShell-([A-Za-z]+): (.*)$", line)
        if m:
            meta[m.group(1).lower()] = m.group(2).strip()
    return meta


def _entries(top: Path, deadline: _Deadline, wt12: str) -> list[dict]:
    """이 작업 폴더의 체크포인트 ref 전부(세션 키·seq 순). 메타데이터는 커밋 메시지 트레일러에서 읽는다."""
    fmt = "%(refname)%01%(objectname)%01%(tree)%01%(parent)%01%(committerdate:unix)%01%(contents)%02"
    cp = _git(top, deadline, "for-each-ref", f"--format={fmt}", f"{REF_PREFIX}{wt12}/", check=False)
    if cp.returncode != 0:
        return []
    out: list[dict] = []
    for rec in cp.stdout.decode("utf-8", errors="replace").split("\x02"):
        rec = rec.strip("\n")
        if not rec:
            continue
        parts = rec.split("\x01")
        if len(parts) != 6:
            continue
        ref, commit, tree, parent, ts, message = parts
        rest = ref[len(REF_PREFIX):]                       # <wt12>/<sess>/<seq> | <wt12>/restore/<utc>
        segs = rest.split("/")
        if len(segs) != 3:
            continue
        meta = _parse_meta(message)
        if meta.get("checkpoint") != "1":                  # 우리가 만든 것이 아니다
            continue
        is_restore = segs[1] == "restore"
        try:
            seq = int(segs[2])
        except ValueError:
            continue
        out.append({
            "ref": ref, "id": f"v1/{rest}", "wt12": segs[0], "sess": "" if is_restore else segs[1],
            "seq": seq, "restore": is_restore, "commit": commit, "tree": tree,
            "parent": parent.split(" ")[0] if parent else "",
            "created": int(ts) if ts.isdigit() else 0,
            "kind": meta.get("kind", ""), "turn": int(meta["turn"]) if meta.get("turn", "").isdigit() else None,
            "agent": meta.get("agent", ""), "trusted": meta.get("trusted") == "yes",
            "skipped": int(meta["skipped"]) if meta.get("skipped", "").isdigit() else 0,
        })
    out.sort(key=lambda e: (e["sess"], e["seq"]))
    return out


# --- 캡처 ----------------------------------------------------------------------


def capture(top, *, kind: str, sess_key: str = "", agent: str = "", turn: Optional[int] = None,
            trusted: bool = True, timeout: Optional[float] = None) -> dict:
    """작업 폴더 상태를 체크포인트 ref 하나로 남긴다. 결과는 항상 dict(예외를 던지지 않는다):

    `status`: `ok` | `unchanged`(직전 체크포인트와 트리·HEAD가 같아 ref 없음) | `norepo` | `unsupported`(sparse checkout)
    | `skipped_busy`(병합·리베이스 진행 중) | `too_large` | `busy`(같은 작업 폴더의 다른 캡처가 잠금을 쥠) | `timeout` | `error`.
    `ok`에서만 `ref`·`id`·`seq`·`tree`·`commit`이 채워진다. `skipped`는 큰 파일·거부 경로 등으로 뺀 경로 수, `paths`는 담은 경로 수.
    """
    if kind not in KINDS:
        return _result("error", reason="bad_kind")
    restore = kind == "pre-restore"
    if not restore and not _SESS_RE.match(sess_key or ""):
        return _result("error", reason="bad_session_key")
    agent = agent if _AGENT_RE.match(agent or "") else "unknown"
    deadline = _Deadline(timeout if timeout and timeout > 0 else timeout_seconds())
    top = Path(top)
    try:
        repo = _open(top, deadline)
        if repo is None:
            return _result("norepo")
        lock = _lock_for(repo.git_dir)
        try:
            with lock.hold(LOCK_WAIT_SECONDS, op="ckpt.capture"):
                return _capture_locked(repo, deadline, kind=kind, sess_key=sess_key, agent=agent, turn=turn,
                                       trusted=trusted, restore=restore)
        except LockTimeout:
            return _result("busy")
    except _Timeout:
        logger.info("[ckpt] 캡처 기한 초과")
        return _result("timeout")
    except _GitError as e:
        logger.info("[ckpt] 캡처 실패: %s", e)
        return _result("error", reason=e.what)
    except OSError as e:
        logger.info("[ckpt] 캡처 실패(OS): %s", type(e).__name__)
        return _result("error", reason=type(e).__name__)


def _capture_locked(repo: _Repo, deadline: _Deadline, *, kind: str, sess_key: str, agent: str,
                    turn: Optional[int], trusted: bool, restore: bool) -> dict:
    if _config_bool(repo, deadline, "core.sparseCheckout"):
        return _result("unsupported", reason="sparse_checkout")
    if _busy_state(repo.git_dir):
        return _result("skipped_busy")
    if repo.git_dir not in _cleaned:
        _cleaned.add(repo.git_dir)
        _clean_stale_indexes(repo.git_dir)

    top = repo.top
    index = os.path.join(repo.git_dir, "fsh-ckpt-index-" + uuid.uuid4().hex)
    user_index = os.path.join(repo.git_dir, "index")
    try:
        # 1) 임시 인덱스. 사용자 인덱스를 복사해(파일 상태 캐시 재사용) 바뀐 파일만 해시한다. split index면 공유 파일을 잃으므로
        #    HEAD에서 새로 읽는다(느린 경로). 사용자 인덱스는 읽기만 한다.
        env = {"GIT_INDEX_FILE": index, "GIT_LITERAL_PATHSPECS": "1"}
        if os.path.exists(user_index) and not _config_bool(repo, deadline, "core.splitIndex"):
            shutil.copyfile(user_index, index)
        elif repo.head:
            _git(top, deadline, *_INDEX_CFG, "read-tree", repo.head, env=env)

        # 2) 대상 경로: 바뀜·지워짐(추적 중) + 추적 안 됨(ignore 제외). 파이썬에서 거른다.
        tracked = _split_nul(_git(top, deadline, "ls-files", "-z", "-m", "-d").stdout)
        untracked = _split_nul(_git(top, deadline, "ls-files", "-z", "-o", "--exclude-standard").stdout)
        file_cap, total_cap = max_file_bytes(), max_total_bytes()
        keep: list[bytes] = []
        skipped = 0
        total = 0
        seen: set[bytes] = set()
        for is_untracked, group in ((False, tracked), (True, untracked)):
            for p in group:
                if p in seen:
                    continue
                seen.add(p)
                name = _fs(p)
                if p.endswith(b"/"):                           # 추적 안 된 중첩 저장소 — 담지 않는다
                    skipped += 1
                    continue
                if fsguard.repo_relpath_denied(name):
                    skipped += 1
                    continue
                size = 0
                try:
                    st = os.lstat(os.path.join(repo.toplevel, name))
                    if (st.st_mode & 0o170000) == 0o100000:    # 일반 파일만 크기를 센다(심링크·특수 파일은 0)
                        size = st.st_size
                except OSError:
                    pass                                      # 지워진 파일(-d)
                if is_untracked and size > file_cap:
                    skipped += 1
                    continue
                total += size
                keep.append(p)
        if len(keep) > max_paths() or total > total_cap:
            return _result("too_large", skipped=skipped, paths=len(keep))

        # 3) add -A → write-tree
        if keep:
            _git(top, deadline, *_INDEX_CFG, "-c", "core.safecrlf=false", "add", "-A",
                 "--pathspec-from-file=-", "--pathspec-file-nul", env=env, input=b"\0".join(keep))
        tree = _text(_git(top, deadline, *_INDEX_CFG, "write-tree", env=env).stdout)

        entries = _entries(top, deadline, repo.wt12)
        if not restore:
            mine = [e for e in entries if e["sess"] == sess_key]
            last = max(mine, key=lambda e: e["seq"]) if mine else None
            if last and last["tree"] == tree and last["parent"] == repo.head:
                return _result("unchanged", tree=tree, skipped=skipped, paths=len(keep))
            seq = (last["seq"] + 1) if last else 1
        else:
            seq = int(time.time())

        # 4) commit-tree → update-ref. 같은 seq를 다른 프로세스가 먼저 만들었으면 다음 번호로 다시 시도한다.
        message = _message(kind, turn if turn is not None else seq, agent, trusted, skipped)
        cargs = ["-c", "commit.gpgSign=false", "commit-tree", tree, "-m", message] + (["-p", repo.head] if repo.head else [])
        commit = _text(_git(top, deadline, *cargs, env=dict(_IDENT)).stdout)
        for _ in range(5):
            ref = _ref(repo.wt12, "restore" if restore else sess_key, seq)
            cp = _git(top, deadline, "-c", "core.fsync=objects,reference", "-c", "core.fsyncMethod=fsync",
                      "update-ref", ref, commit, "", check=False)
            if cp.returncode == 0:
                return _result("ok", ref=ref, id="v1/" + ref[len(REF_PREFIX):], seq=seq, tree=tree, commit=commit,
                               skipped=skipped, paths=len(keep))
            seq += 1
        return _result("error", reason="update-ref", tree=tree)
    finally:
        for f in (index, index + ".lock"):
            try:
                os.unlink(f)
            except OSError:
                pass


def _ref(wt12: str, sess: str, seq: int) -> str:
    return f"{REF_PREFIX}{wt12}/{sess}/{seq:010d}" if sess == "restore" else f"{REF_PREFIX}{wt12}/{sess}/{seq:06d}"


def _message(kind: str, turn: int, agent: str, trusted: bool, skipped: int) -> str:
    # 경로·지시문·tmux 이름은 넣지 않는다.
    return ("FarShell checkpoint\n\n"
            "FarShell-Checkpoint: 1\n"
            f"FarShell-Kind: {kind}\n"
            f"FarShell-Turn: {int(turn)}\n"
            f"FarShell-Agent: {agent}\n"
            f"FarShell-Trusted: {'yes' if trusted else 'no'}\n"
            f"FarShell-Skipped: {int(skipped)}\n")


# --- 읽기 ----------------------------------------------------------------------


def list_refs(top, *, timeout: Optional[float] = None) -> list[dict]:
    """이 작업 폴더의 체크포인트 목록(세션 키·seq 순). git 저장소가 아니거나 실패하면 빈 목록."""
    deadline = _Deadline(timeout if timeout and timeout > 0 else timeout_seconds())
    try:
        repo = _open(Path(top), deadline)
        return _entries(repo.top, deadline, repo.wt12) if repo else []
    except (_Timeout, _GitError, OSError):
        return []


def turn_base(entries: list[dict], to_id: str) -> Optional[dict]:
    """턴의 비교 기준: 같은 턴의 신뢰된 `pre-turn` → 없으면 앞선 `turn-end`/`baseline` 중 가장 최근 것 → 없으면 None.
    (`pre-turn`을 못 믿을 때는 턴 사이에 사람이 고친 것이 섞일 수 있다 — 표시는 호출자가 한다.)"""
    to = next((e for e in entries if e["id"] == to_id), None)
    if to is None or to["restore"]:
        return None
    mine = [e for e in entries if e["sess"] == to["sess"] and e["seq"] < to["seq"]]
    pre = [e for e in mine if e["kind"] == "pre-turn" and e["trusted"] and e["turn"] == to["turn"]]
    if pre:
        return max(pre, key=lambda e: e["seq"])
    earlier = [e for e in mine if e["kind"] in ("turn-end", "baseline")]
    return max(earlier, key=lambda e: e["seq"]) if earlier else None


def _resolve(entries: list[dict], cid: str) -> Optional[dict]:
    if not ID_RE.match(cid or ""):
        return None
    return next((e for e in entries if e["id"] == cid), None)


def turn_files(top, to_id: str, from_id: Optional[str] = None, *, timeout: Optional[float] = None) -> Optional[dict]:
    """두 체크포인트 사이에 바뀐 파일(`--numstat`). `from_id`를 안 주면 `turn_base`로 정한다(없으면 `to`의 부모 HEAD).
    결과: `{from, to, head_moved, files:[{path, added, deleted, binary}]}`. 없는 id·저장소면 None."""
    deadline = _Deadline(timeout if timeout and timeout > 0 else timeout_seconds())
    try:
        repo = _open(Path(top), deadline)
        if repo is None:
            return None
        entries = _entries(repo.top, deadline, repo.wt12)
        to = _resolve(entries, to_id)
        if to is None:
            return None
        if from_id:
            base = _resolve(entries, from_id)
            if base is None:
                return None
            base_obj, base_parent = base["commit"], base["parent"]
            from_label = from_id
        else:
            b = turn_base(entries, to_id)
            from_label = b["id"] if b else None
            base_obj, base_parent = (b["commit"], b["parent"]) if b else (to["parent"], to["parent"])
        if not base_obj:
            return {"from": from_label, "to": to_id, "head_moved": False, "files": []}   # 첫 커밋 전 baseline 등
        cp = _git(repo.top, deadline, "diff", "--numstat", "-z", "--no-renames", *git_safe.DIFF_FLAGS, base_obj, to["commit"])
        files = []
        for rec in _split_nul(cp.stdout):
            m = re.match(rb"^(-|\d+)\t(-|\d+)\t(.*)$", rec, re.S)
            if not m:
                continue
            name = _fs(m.group(3))
            if fsguard.repo_relpath_denied(name):         # 이중 안전: 캡처에서 이미 빠졌어야 한다
                continue
            binary = m.group(1) == b"-"
            files.append({"path": name, "added": 0 if binary else int(m.group(1)),
                          "deleted": 0 if binary else int(m.group(2)), "binary": binary})
        return {"from": from_label, "to": to_id,
                "head_moved": bool(base_parent != to["parent"]), "files": files}
    except (_Timeout, _GitError, OSError):
        return None


def diff(top, to_id: str, from_id: Optional[str] = None, file: str = "", *, timeout: Optional[float] = None) -> Optional[dict]:
    """한 파일(또는 전체)의 체크포인트 간 diff 텍스트. 열람 규칙에 걸리는 파일 본문은 가리고 1MB로 자른다.
    결과: `{diff, truncated}`. 없는 id·저장소·위험한 `file`이면 None."""
    from routes.git import MAX_DIFF_BYTES, _bad_repo_relpath, _redact_diff
    if file and (_bad_repo_relpath(file) or file.startswith("-") or "\0" in file):
        return None
    deadline = _Deadline(timeout if timeout and timeout > 0 else timeout_seconds())
    try:
        repo = _open(Path(top), deadline)
        if repo is None:
            return None
        entries = _entries(repo.top, deadline, repo.wt12)
        to = _resolve(entries, to_id)
        if to is None:
            return None
        if from_id:
            base = _resolve(entries, from_id)
            base_obj = base["commit"] if base else None
            if base is None:
                return None
        else:
            b = turn_base(entries, to_id)
            base_obj = b["commit"] if b else (to["parent"] or None)
        if not base_obj:
            return {"diff": "", "truncated": False}
        args = ["diff", "--no-color", *git_safe.DIFF_FLAGS, base_obj, to["commit"]]
        if file:
            args += ["--", file]
        out = _redact_diff(_text_keep(_git(repo.top, deadline, *args, readable_paths=True).stdout))
        raw = out.encode()
        truncated = len(raw) > MAX_DIFF_BYTES
        if truncated:
            out = raw[:MAX_DIFF_BYTES].decode("utf-8", errors="ignore")
        return {"diff": out, "truncated": truncated}
    except (_Timeout, _GitError, OSError):
        return None


def _text_keep(b: bytes) -> str:
    return b.decode("utf-8", errors="replace")


# --- 정리 ----------------------------------------------------------------------


def gc(top, *, now: Optional[float] = None, timeout: Optional[float] = None) -> dict:
    """이 작업 폴더의 오래된 ref를 지운다(다른 작업 폴더의 ref는 건드리지 않는다). 객체는 건드리지 않는다.

    규칙: 세션당 baseline 1개 + 최근 턴 체크포인트 `VT_CKPT_KEEP_TURNS`(30)개 / 마지막 체크포인트가 `VT_CKPT_KEEP_DAYS`(14)일
    넘은 세션은 통째로 / 복원 안전판 `VT_CKPT_KEEP_RESTORE_DAYS`(7)일 / 작업 폴더당 총 `VT_CKPT_MAX_REFS`(500)개(오래된 것부터).
    결과: `{deleted, kept}`. 실패하면 `{deleted: 0, kept: 0, error: True}`."""
    now = time.time() if now is None else now
    deadline = _Deadline(timeout if timeout and timeout > 0 else timeout_seconds())
    try:
        repo = _open(Path(top), deadline)
        if repo is None:
            return {"deleted": 0, "kept": 0}
        try:
            with _lock_for(repo.git_dir).hold(LOCK_WAIT_SECONDS, op="ckpt.gc"):
                entries = _entries(repo.top, deadline, repo.wt12)
                doomed = _select_gc(entries, now)
                if doomed:
                    stdin = "".join(f"delete {e['ref']}\n" for e in doomed).encode()
                    _git(repo.top, deadline, "update-ref", "--stdin", input=stdin)
                return {"deleted": len(doomed), "kept": len(entries) - len(doomed)}
        except LockTimeout:
            return {"deleted": 0, "kept": 0, "busy": True}
    except (_Timeout, _GitError, OSError):
        return {"deleted": 0, "kept": 0, "error": True}


def _select_gc(entries: list[dict], now: float) -> list[dict]:
    doomed: dict[str, dict] = {}
    day = 86400
    sessions: dict[str, list[dict]] = {}
    for e in entries:
        if e["restore"]:
            if now - e["created"] > keep_restore_days() * day:
                doomed[e["ref"]] = e
        else:
            sessions.setdefault(e["sess"], []).append(e)
    for items in sessions.values():
        items.sort(key=lambda e: e["seq"])
        newest = items[-1]
        if now - newest["created"] > keep_days() * day:
            doomed.update({e["ref"]: e for e in items})
            continue
        baseline = next((e for e in items if e["kind"] == "baseline"), None)
        rest = [e for e in items if e is not baseline]
        for e in rest[:-keep_turns()] if len(rest) > keep_turns() else []:
            doomed[e["ref"]] = e
    # 총량 상한: 남은 것 중 오래된 것부터
    alive = sorted((e for e in entries if e["ref"] not in doomed), key=lambda e: (e["created"], e["seq"]))
    over = len(alive) - max_refs()
    for e in alive[:over] if over > 0 else []:
        doomed[e["ref"]] = e
    return list(doomed.values())
