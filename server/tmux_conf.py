"""FarShell tmux 설정 — 레포 conf 가 늘 기준, 개인 설정은 tmux.local.conf 에.

설정 층
  1. `VT_TMUX_CONF`        전체 교체(격리 시험·`scripts/profile_sandbox.py` 용). 있으면 이것만 쓴다.
  2. `config/vt-tmux.conf` 레포 기준 설정. 끝에서 `~/.config/vt/tmux.local.conf` 를 `-q` 로 읽는다.

예전에는 install.sh 가 레포 conf 를 `~/.config/vt/tmux.conf` 로 **한 번만** 복사했고, 그 사본이
레포 conf 보다 먼저 잡혀서 이후 레포 변경이 영영 반영되지 않았다. `migrate_legacy()` 가 그 사본을
치운다 — 지우지 않고 이름만 바꾼다:
  - 레포가 배포한 적 있는 판과 바이트까지 같으면(손대지 않은 사본) → `tmux.conf.bak-YYYYMMDD`
  - 다르면(사용자가 고친 것) → `tmux.conf.legacy` + 필요한 줄을 tmux.local.conf 로 옮기라는 안내

`reload()` 는 fsh tmux 서버가 **이미 떠 있을 때만** `source-file` 로 다시 읽힌다. tmux 서버를 새로
띄우거나 죽이지 않는다(세션이 끊긴다). 다시 읽어도 예전 설정에만 있던 값은 남는다 — 완전히 깨끗한
상태는 tmux 서버를 재시작해야 한다.

확장 키 시험: `extkeys on|off|status` 가 tmux.local.conf 의 표식 구간(`# >>> fsh extkeys` … `# <<< fsh extkeys`)
만 쓰고 지운다. 사용자가 쓴 다른 줄은 건드리지 않는다. 기본(저장소 conf)은 extended-keys off 그대로다.

CLI: `python server/tmux_conf.py migrate|reload|status|doctor|extkeys [on|off|status] [--dry-run]`
(`fsh tmux reload`, `fsh doctor`, `fsh sync`, install.sh 가 부른다.)
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import tmux_runner
from i18n import tr

REPO_CONF = Path(__file__).resolve().parent.parent / "config" / "vt-tmux.conf"

# 레포가 배포한 적 있는 config/vt-tmux.conf 판의 sha256. 이 값과 같은 옛 사본은 사용자가 손대지
# 않은 것이므로 백업 이름으로 치워도 잃는 것이 없다. 다시 만드는 법:
#   for s in $(git log --all --format=%H -- config/vt-tmux.conf); do
#     git show "$s:config/vt-tmux.conf" | shasum -a 256; done
# install.sh 가 더는 복사하지 않으므로 이 목록은 늘어나지 않는다(지금 레포 판은 실행 중에 따로 센다).
SHIPPED_SHA256 = frozenset({
    "fbd2c0ea1130835f1ccc814f3ccc7b5199d8e8007e67ae69193ecd9e34385b5f",  # 4870e14
    "3cf71a119f3eceb9a6f2ca74f1b1273b0440afffdd43bb9895d2a78fa895cdc9",  # f4f9fbd
    "ff42848ac54bcec2080658feea076c2fd75e545d23b8703ef708b27a230fcd5a",  # dab1e65
})

# 레포 conf 가 끝에서 세우는 표시. 실행 중인 tmux 에 이 값이 있으면 레포 conf 가 (시작 때든
# reload 로든) 들어간 것이다. `#{config_files}` 는 시작할 때 읽은 파일만 담고 source-file 로는
# 바뀌지 않아서 이것만으로는 reload 여부를 알 수 없다.
LAYERED_MARK = "@fsh_conf_layered"


def _home(home: Optional[Path]) -> Path:
    return Path(home) if home is not None else Path.home()


def conf_dir(home: Optional[Path] = None) -> Path:
    return _home(home) / ".config" / "vt"


def legacy_path(home: Optional[Path] = None) -> Path:
    return conf_dir(home) / "tmux.conf"


def local_path(home: Optional[Path] = None) -> Path:
    return conf_dir(home) / "tmux.local.conf"


def override_conf() -> Optional[str]:
    """VT_TMUX_CONF 가 설정돼 있으면 그 값. 있으면 홈의 파일은 일절 건드리지 않는다."""
    return os.environ.get("VT_TMUX_CONF") or None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def known_hashes() -> frozenset[str]:
    hashes = set(SHIPPED_SHA256)
    try:
        hashes.add(_sha256(REPO_CONF))
    except OSError:
        pass
    return frozenset(hashes)


def _free_name(base: Path) -> Path:
    """base 가 비어 있으면 base, 아니면 base.1, base.2 … 중 처음 비어 있는 이름. 덮어쓰지 않는다."""
    if not os.path.lexists(base):
        return base
    n = 1
    while os.path.lexists(f"{base}.{n}"):
        n += 1
    return Path(f"{base}.{n}")


def leftover_legacy(home: Optional[Path] = None) -> list[Path]:
    """사용자가 고쳐서 치워 둔 `tmux.conf.legacy*` — 필요한 줄을 옮긴 뒤 사용자가 지운다."""
    d = conf_dir(home)
    try:
        return sorted(p for p in d.glob("tmux.conf.legacy*") if p.is_file())
    except OSError:
        return []


@dataclass
class MigrateResult:
    # override | absent | backup | legacy | error
    action: str
    src: Optional[Path] = None
    dest: Optional[Path] = None
    error: str = ""


def migrate_legacy(home: Optional[Path] = None, dry_run: bool = False,
                   today: Optional[datetime.date] = None) -> MigrateResult:
    """옛 `~/.config/vt/tmux.conf` 사본을 치운다. 멱등이며 아무것도 지우지 않는다."""
    if override_conf():
        return MigrateResult("override")
    src = legacy_path(home)
    if not src.is_file():
        return MigrateResult("absent")
    try:
        untouched = _sha256(src) in known_hashes()
    except OSError as e:
        return MigrateResult("error", src, error=str(e))
    if untouched:
        stamp = (today or datetime.date.today()).strftime("%Y%m%d")
        action, dest = "backup", _free_name(src.with_name(f"tmux.conf.bak-{stamp}"))
    else:
        action, dest = "legacy", _free_name(src.with_name("tmux.conf.legacy"))
    if not dry_run:
        try:
            os.rename(src, dest)
        except OSError as e:
            return MigrateResult("error", src, dest, error=str(e))
    return MigrateResult(action, src, dest)


def server_running() -> bool:
    """fsh 소켓의 tmux 서버가 떠 있는가. list-sessions 는 서버를 새로 띄우지 않는다."""
    rc, _, _ = tmux_runner.run(["list-sessions"])
    return rc == 0


def active_conf() -> Optional[str]:
    """지금 fsh 가 tmux 에 넘기는 설정 파일(VT_TMUX_CONF 또는 레포 conf)."""
    return tmux_runner.VT_TMUX_CONF


@dataclass
class ReloadResult:
    # not-running | ok | error | no-conf | would-reload
    action: str
    conf: Optional[str] = None
    error: str = ""


def reload(dry_run: bool = False) -> ReloadResult:
    """떠 있는 fsh tmux 서버에 설정을 다시 읽힌다. 서버를 띄우거나 죽이지 않는다."""
    conf = active_conf()
    if not conf:
        return ReloadResult("no-conf")
    if not server_running():
        return ReloadResult("not-running", conf)
    if dry_run:
        return ReloadResult("would-reload", conf)
    rc, _, err = tmux_runner.run(["source-file", conf], timeout=5.0)
    if rc != 0:
        return ReloadResult("error", conf, err.decode("utf-8", errors="replace").strip() or f"rc={rc}")
    return ReloadResult("ok", conf)


@dataclass
class Status:
    running: bool
    conf: Optional[str]
    override: Optional[str]
    config_files: list[str]
    layered: bool
    legacy_location: Path
    legacy: Optional[Path]
    leftovers: list[Path]
    local: Path
    local_exists: bool


def status(home: Optional[Path] = None) -> Status:
    running = server_running()
    files: list[str] = []
    layered = False
    if running:
        out = tmux_runner.run_text(["display-message", "-p", "#{config_files}"]) or ""
        files = [f for f in out.strip().split(",") if f]
        layered = (tmux_runner.run_text(["show-options", "-gqv", LAYERED_MARK]) or "").strip() == "1"
    legacy = legacy_path(home)
    local = local_path(home)
    return Status(running=running, conf=active_conf(), override=override_conf(),
                  config_files=files, layered=layered, legacy_location=legacy,
                  legacy=legacy if legacy.is_file() else None,
                  leftovers=leftover_legacy(home), local=local, local_exists=local.is_file())


# ── 사람이 읽는 출력 ──────────────────────────────────────────────────────────
def describe_migrate(r: MigrateResult, dry_run: bool = False, home: Optional[Path] = None) -> list[str]:
    if r.action == "override":
        return ["  ⓘ " + tr("tmux 설정: VT_TMUX_CONF 를 쓰는 중이라 옛 사본 정리를 건너뜁니다")]
    if r.action == "absent":
        return []
    if r.action == "error":
        return ["  ✗ " + tr("tmux 설정: {path} 을(를) 옮기지 못했습니다: {err}", path=r.src, err=r.error)]
    if dry_run:
        return ["  ⚠ " + tr("tmux 설정: 옛 사본 {path} 을(를) {dest} 로 옮길 예정입니다", path=r.src, dest=r.dest)]
    if r.action == "backup":
        return ["  ✓ " + tr("tmux 설정: 손대지 않은 옛 사본을 {dest} 로 옮겼습니다 — 이제 레포 설정을 씁니다",
                            dest=r.dest)]
    return ["  ⚠ " + tr("tmux 설정: 직접 고친 옛 설정을 {dest} 로 옮겼습니다", dest=r.dest),
            "    " + tr("필요한 줄은 {local} 로 옮기세요 (레포 설정 뒤에 읽혀 덮어씁니다)", local=local_path(home))]


def describe_reload(r: ReloadResult) -> list[str]:
    if r.action == "no-conf":
        return ["  ✗ " + tr("tmux 설정 파일을 찾지 못했습니다 ({path})", path=REPO_CONF)]
    if r.action == "not-running":
        return ["  ⓘ " + tr("tmux 서버가 꺼져 있어 다시 읽지 않았습니다 — 다음 시작 때 적용됩니다")]
    if r.action == "would-reload":
        return ["  ⓘ " + tr("떠 있는 tmux 서버에 설정을 다시 읽힐 예정입니다 ({conf})", conf=r.conf)]
    if r.action == "error":
        return ["  ✗ " + tr("tmux 설정을 다시 읽지 못했습니다: {err}", err=r.error)]
    return ["  ✓ " + tr("tmux 설정을 다시 읽었습니다 ({conf})", conf=r.conf),
            "    " + tr("설정에서 지운 값은 실행 중인 tmux 에 남습니다 — 지킬 세션이 없을 때만 tmux 서버를 재시작하면 완전히 새로 읽힙니다")]


def doctor_lines(st: Status) -> tuple[str, list[str]]:
    """`fsh doctor` 「tmux 설정」 한 칸: (ok|warn, [본문, 덧붙일 줄…])."""
    level = "ok"
    notes: list[str] = []
    if st.override:
        head = tr("VT_TMUX_CONF 사용 중: {path}", path=st.override)
    elif not st.running:
        head = tr("tmux 서버 꺼짐 — 다음 시작 때 레포 설정을 씁니다")
    elif st.layered:
        head = tr("실행 중인 tmux 에 레포 설정이 들어가 있음")
        if any(Path(f) == st.legacy_location for f in st.config_files):
            notes.append(tr("옛 사본으로 시작한 서버라 옛 값이 남아 있을 수 있습니다 — 지킬 세션이 없을 때만 tmux 서버를 재시작하세요"))
    else:
        level = "warn"
        started = ", ".join(st.config_files) or "?"
        head = tr("실행 중인 tmux 에 레포 설정이 아직 안 들어감 (시작 설정: {files}) — 'fsh tmux reload'", files=started)
    if st.legacy and not st.override:
        level = "warn"
        notes.append(tr("옛 사본 {path} 이(가) 남아 있습니다 — 'fsh tmux reload' 가 치웁니다", path=st.legacy))
    for p in st.leftovers:
        level = "warn"
        notes.append(tr("직접 고친 옛 설정 {path} 이(가) 남아 있습니다 — 필요한 줄을 tmux.local.conf 로 옮긴 뒤 지우세요", path=p))
    notes.append(tr("개인 설정 {path}: 있음", path=st.local) if st.local_exists
                 else tr("개인 설정 {path}: 없음", path=st.local))
    return level, [head, *notes]


# ── 확장 키(extended-keys) 시험 스니펫 ─────────────────────────────────────────
# 웹이 kitty 프로토콜로 보내는 CSI u 를 tmux 가 안쪽 앱에 넘기게 하는 설정. 기본은 꺼 둔다(시험 전).
# terminal-features 는 `set -as`(덧붙임)로 쓰면 reload 할 때마다 같은 줄이 쌓이므로 번호 지정
# 대입을 쓴다 — 몇 번을 읽어도 한 줄이고 `set -u` 로 그 한 줄만 지울 수 있다(tmux 3.7c 로 확인).
EXTKEYS_BEGIN = "# >>> fsh extkeys"
EXTKEYS_END = "# <<< fsh extkeys"
EXTKEYS_FEATURE_OPT = "terminal-features[90]"
EXTKEYS_FEATURE_VAL = "xterm*:extkeys"
EXTKEYS_MIN_TMUX = (3, 2)  # extended-keys·terminal-features 는 3.2 에서 생겼다
EXTKEYS_BLOCK = (
    f"{EXTKEYS_BEGIN}\n"
    "# fsh tmux extkeys on|off 가 관리하는 구간 — 손으로 고치지 마세요(다른 줄은 이 밖에 두세요).\n"
    "set -sq extended-keys on\n"
    f"set -sq '{EXTKEYS_FEATURE_OPT}' '{EXTKEYS_FEATURE_VAL}'\n"
    f"{EXTKEYS_END}\n"
)


class ExtkeysError(Exception):
    pass


def _find_block(lines: list[str]) -> Optional[tuple[int, int]]:
    """표식 구간의 (시작, 끝) 줄 번호(끝 포함). 없으면 None. 짝이 안 맞으면 ExtkeysError."""
    begins = [i for i, ln in enumerate(lines) if ln.strip() == EXTKEYS_BEGIN]
    ends = [i for i, ln in enumerate(lines) if ln.strip() == EXTKEYS_END]
    if not begins and not ends:
        return None
    if len(begins) != 1 or len(ends) != 1 or ends[0] < begins[0]:
        raise ExtkeysError(tr("표식 구간({begin} … {end})의 짝이 맞지 않습니다 — 파일을 직접 고쳐 주세요",
                              begin=EXTKEYS_BEGIN, end=EXTKEYS_END))
    return begins[0], ends[0]


def extkeys_in_file(home: Optional[Path] = None) -> bool:
    p = local_path(home)
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    except (OSError, UnicodeDecodeError) as e:
        raise ExtkeysError(str(e))
    return _find_block(text.splitlines()) is not None


def _atomic_write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = p.stat().st_mode & 0o777
    except FileNotFoundError:
        mode = 0o600
    tmp = p.with_name(p.name + f".tmp{os.getpid()}")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, p)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def extkeys_edit(on: bool, home: Optional[Path] = None, dry_run: bool = False) -> str:
    """tmux.local.conf 의 표식 구간을 켜거나 지운다. 멱등. 반환: changed | unchanged."""
    p = local_path(home)
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    except (OSError, UnicodeDecodeError) as e:
        raise ExtkeysError(str(e))
    lines = text.splitlines(keepends=True)
    span = _find_block([ln.rstrip("\r\n") for ln in lines])
    if on:
        if span is not None:
            cur = "".join(lines[span[0]:span[1] + 1])
            if cur == EXTKEYS_BLOCK:
                return "unchanged"
            new_lines = lines[:span[0]] + [EXTKEYS_BLOCK] + lines[span[1] + 1:]
        else:
            sep = "" if not text or text.endswith("\n") else "\n"
            if text.strip():
                sep += "\n"
            new_lines = lines + ([sep] if sep else []) + [EXTKEYS_BLOCK]
    else:
        if span is None:
            return "unchanged"
        new_lines = lines[:span[0]] + lines[span[1] + 1:]
        # 켤 때 구간 앞에 넣은 빈 줄이 남지 않게: 앞이 빈 줄이고 뒤가 끝이거나 빈 줄이면 앞 빈 줄을 뺀다
        i = span[0]
        if i > 0 and new_lines[i - 1].strip() == "" and (i >= len(new_lines) or new_lines[i].strip() == ""):
            new_lines.pop(i - 1)
    if not dry_run:
        try:
            _atomic_write(p, "".join(new_lines))
        except OSError as e:
            raise ExtkeysError(str(e))
    return "changed"


def tmux_version() -> Optional[tuple[int, int]]:
    """`tmux -V` 의 (major, minor). 서버를 띄우지 않는다. 못 읽으면 None."""
    try:
        out = subprocess.run(["tmux", "-V"], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"(\d+)\.(\d+)", out)
    return (int(m.group(1)), int(m.group(2))) if m else None


@dataclass
class ExtkeysStatus:
    server: Optional[str]      # 떠 있는 서버의 extended-keys 값(on|off|always), 서버가 꺼져 있으면 None
    feature: bool              # 떠 있는 서버 terminal-features 에 extkeys 가 들어 있는가
    in_file: bool              # tmux.local.conf 에 표식 구간이 있는가
    version: Optional[tuple[int, int]]
    local: Path
    override: Optional[str]


def extkeys_status(home: Optional[Path] = None) -> ExtkeysStatus:
    server: Optional[str] = None
    feature = False
    if server_running():
        server = (tmux_runner.run_text(["show-options", "-sv", "extended-keys"]) or "").strip() or None
        feats = tmux_runner.run_text(["show-options", "-sv", "terminal-features"]) or ""
        feature = "extkeys" in feats
    return ExtkeysStatus(server, feature, extkeys_in_file(home), tmux_version(),
                         local_path(home), override_conf())


def extkeys_live_off() -> bool:
    """reload 는 설정에서 지운 값을 되돌리지 못하므로, 떠 있는 서버에 끄는 값을 직접 넣는다."""
    if not server_running():
        return True
    rc1, _, _ = tmux_runner.run(["set-option", "-sq", "extended-keys", "off"], timeout=5.0)
    rc2, _, _ = tmux_runner.run(["set-option", "-squ", EXTKEYS_FEATURE_OPT], timeout=5.0)
    return rc1 == 0 and rc2 == 0


def describe_extkeys_status(st: ExtkeysStatus) -> list[str]:
    ver = f"{st.version[0]}.{st.version[1]}" if st.version else "?"
    lines = []
    if st.server is None:
        lines.append("  ⓘ " + tr("tmux 서버가 꺼져 있습니다 — 다음 시작 때 파일 구간대로 적용됩니다"))
    else:
        lines.append("  ✓ " + tr("떠 있는 tmux: extended-keys = {v}, terminal-features 의 extkeys = {f}",
                                  v=st.server, f=tr("있음") if st.feature else tr("없음")))
    lines.append("  ✓ " + (tr("{path}: 확장 키 구간 있음", path=st.local) if st.in_file
                           else tr("{path}: 확장 키 구간 없음", path=st.local)))
    lines.append("  ⓘ " + tr("tmux 버전 {v}", v=ver))
    if st.version and st.version < EXTKEYS_MIN_TMUX:
        lines.append("  ⚠ " + tr("tmux 3.2 이상이 필요합니다 — 이 버전에서는 켜도 적용되지 않습니다"))
    if st.override:
        lines.append("  ⚠ " + tr("VT_TMUX_CONF 사용 중이라 tmux.local.conf 를 읽지 않습니다 — 구간이 적용되지 않습니다"))
    on_live = st.server in ("on", "always") and st.feature
    if st.in_file != on_live and st.server is not None:
        lines.append("  ⚠ " + tr("파일과 떠 있는 tmux 가 다릅니다 — 'fsh tmux extkeys {m}' 또는 'fsh tmux reload'",
                                  m="on" if st.in_file else "off"))
    return lines


def run_extkeys(mode: str, dry_run: bool = False, home: Optional[Path] = None) -> int:
    if mode == "status":
        try:
            for ln in describe_extkeys_status(extkeys_status(home)):
                print(ln)
        except ExtkeysError as e:
            print("  ✗ " + str(e))
            return 1
        return 0
    on = mode == "on"
    ver = tmux_version()
    if on and ver and ver < EXTKEYS_MIN_TMUX:
        print("  ✗ " + tr("tmux 3.2 이상이 필요합니다 (지금 {v})", v=f"{ver[0]}.{ver[1]}"))
        return 1
    try:
        res = extkeys_edit(on, home, dry_run)
    except ExtkeysError as e:
        print("  ✗ " + tr("tmux.local.conf 를 고치지 못했습니다: {err}", err=str(e)))
        return 1
    p = local_path(home)
    if dry_run:
        if res != "changed":
            print("  ⓘ " + tr("{path}: 이미 그 상태입니다", path=p))
        elif on:
            print("  ⓘ " + tr("{path} 에 확장 키 구간을 쓸 예정입니다", path=p))
        else:
            print("  ⓘ " + tr("{path} 의 확장 키 구간을 지울 예정입니다", path=p))
        return 0
    if res != "changed":
        print("  ✓ " + tr("{path}: 이미 그 상태입니다", path=p))
    elif on:
        print("  ✓ " + tr("{path} 에 확장 키 구간을 썼습니다", path=p))
    else:
        print("  ✓ " + tr("{path} 의 확장 키 구간을 지웠습니다", path=p))
    if override_conf():
        print("  ⚠ " + tr("VT_TMUX_CONF 사용 중이라 tmux.local.conf 를 읽지 않습니다 — 구간이 적용되지 않습니다"))
        return 0
    r = reload()
    for ln in describe_reload(r):
        print(ln)
    rc = 1 if r.action in ("error", "no-conf") else 0
    if not on and r.action != "no-conf":
        if extkeys_live_off():
            if r.action == "ok":
                print("  ✓ " + tr("떠 있는 tmux 에도 extended-keys off 를 적용했습니다"))
        else:
            print("  ✗ " + tr("떠 있는 tmux 에 끄는 값을 넣지 못했습니다"))
            rc = 1
    if r.action == "ok":
        print("  ⓘ " + tr("이미 붙어 있는 화면은 적용이 늦을 수 있습니다 — 안 되면 탭이나 pane 을 다시 여세요"))
    return rc


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="fsh tmux", description=tr("FarShell tmux 설정 정리·다시 읽기"))
    ap.add_argument("action", nargs="?", default="status",
                    choices=("migrate", "reload", "status", "doctor", "extkeys"))
    ap.add_argument("mode", nargs="?", choices=("on", "off", "status"), default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if args.action == "extkeys":
        return run_extkeys(args.mode or "status", dry_run=args.dry_run)
    if args.mode is not None:
        ap.error(tr("on|off|status 는 extkeys 에만 씁니다"))

    if args.action == "doctor":
        level, lines = doctor_lines(status())
        print(level)
        for ln in lines:
            print(ln)
        return 0

    if args.action == "status":
        st = status()
        level, lines = doctor_lines(st)
        print(("  ✓ " if level == "ok" else "  ⚠ ") + lines[0])
        for ln in lines[1:]:
            print("    " + ln)
        print("    " + tr("설정 파일: {path}", path=st.conf or "-"))
        return 0

    rc = 0
    m = migrate_legacy(dry_run=args.dry_run)
    for ln in describe_migrate(m, args.dry_run):
        print(ln)
    if m.action == "error":
        rc = 1
    if args.action == "reload":
        r = reload(dry_run=args.dry_run)
        for ln in describe_reload(r):
            print(ln)
        if r.action in ("error", "no-conf"):
            rc = 1
    elif m.action == "absent":
        print("  ✓ " + tr("tmux 설정: 옛 사본이 없습니다 — 레포 설정을 씁니다"))
    return rc


if __name__ == "__main__":
    sys.exit(main())
