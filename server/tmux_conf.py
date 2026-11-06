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

CLI: `python server/tmux_conf.py migrate|reload|status|doctor [--dry-run]`
(`fsh tmux reload`, `fsh doctor`, `fsh sync`, install.sh 가 부른다.)
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import os
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


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="fsh tmux", description=tr("FarShell tmux 설정 정리·다시 읽기"))
    ap.add_argument("action", nargs="?", default="status", choices=("migrate", "reload", "status", "doctor"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

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
