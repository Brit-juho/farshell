"""N41 — 누적형(CounterProvider) 어댑터. 소스는 `~/.vt/usage-counter.jsonl`.

**왜 로그 파일인가**: clauth(한도형)는 %를 계산해 주는 외부 데몬이 있지만,
로컬 LLM(ollama 등)에는 그런 게 없다 — "얼마나 썼는지"를 아는 유일한 주체는
그 모델을 돌린 스크립트 자신이다. 그래서 FarShell은 기록 API만 제공하고
(`fsh usage add` / `POST /api/usage/counter`), **에이전트 훅에서 언제 얼마를
기록할지는 사용자 스크립트 몫**이다(60-settings-palette.md §5). FarShell은
저장·표시만 한다.

**동시 쓰기**: `fsh usage add`(CLI)와 `POST /api/usage/counter`(서버)가 동시에
쓸 수 있다 — queue_store.py와 같은 이유로 flock으로 append를 직렬화한다.
append-only라 read-modify-write 경쟁은 없지만, 두 프로세스가 동시에 write()
하면 줄이 섞일 수 있어 락은 그대로 필요하다.

**무한 성장 방지**: 매 append마다 줄 수를 세는 건 비싸므로, 파일 크기가
`MAX_BYTES`를 넘으면 그 시점에 한해 가장 오래된 줄부터 잘라낸다(마지막
`MAX_LINES`줄만 남김) — 사용자가 몇 달을 돌려도 파일이 무한정 안 커진다.

**읽기 보안**: 이 파일은 사용자 스크립트가 직접 쓰는 로컬 로그라 clauth처럼
토큰류가 섞일 걱정은 없지만, 그래도 알려진 키만 통과시킨다(화이트리스트) —
습관을 하나로 유지하기 위해서다.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

MAX_MODEL_LEN = 80
MAX_TOKENS = 100_000_000       # 한 이벤트당 상한 — 오기입 방어(예: 초당 토큰을 누적으로 착각)
MAX_SECONDS = 86_400           # 한 이벤트당 상한(24시간) — 그보다 길면 배치를 나눠 기록해야 한다
MAX_BYTES = 8 * 1024 * 1024    # 8MB — 넘으면 오래된 줄부터 잘라냄
MAX_LINES_KEPT = 50_000        # 잘라낼 때 남기는 줄 수
SPARKLINE_DAYS = 7


def _state_dir() -> Path:
    return Path(os.environ.get("VT_STATE_DIR", "~/.vt")).expanduser()


def _path() -> Path:
    return _state_dir() / "usage-counter.jsonl"


def _lock_path() -> Path:
    return _state_dir() / "usage-counter.lock"


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


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _validate(model, tokens, seconds) -> tuple[Optional[dict], Optional[str]]:
    model = str(model or "").strip()
    if not model:
        return None, "model이 비어 있습니다"
    if len(model) > MAX_MODEL_LEN:
        return None, f"model이 너무 깁니다 (최대 {MAX_MODEL_LEN}자)"
    try:
        tokens = int(tokens)
    except (TypeError, ValueError):
        return None, "tokens는 정수여야 합니다"
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return None, "seconds는 숫자여야 합니다"
    if tokens < 0 or tokens > MAX_TOKENS:
        return None, f"tokens 범위를 벗어났습니다 (0~{MAX_TOKENS})"
    if seconds < 0 or seconds > MAX_SECONDS:
        return None, f"seconds 범위를 벗어났습니다 (0~{MAX_SECONDS})"
    return {"model": model, "tokens": tokens, "seconds": seconds}, None


class CounterJsonlProvider:
    name = "counter_jsonl"

    def __init__(self, path: Optional[Path] = None):
        self._path = path or _path()

    # ── 기록 ─────────────────────────────────────────────────────────────
    def add(self, model, tokens, seconds, *, ts: Optional[float] = None) -> dict:
        norm, err = _validate(model, tokens, seconds)
        if err:
            return {"ok": False, "error": "invalid", "reason": err}
        event = {"ts": ts if ts is not None else time.time(), **norm}
        with _locked():
            p = self._path
            p.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(p.parent, 0o700)
            except OSError:
                pass
            fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
            self._maybe_truncate_unlocked()
        return {"ok": True, "event": event}

    def _maybe_truncate_unlocked(self) -> None:
        p = self._path
        try:
            if p.stat().st_size <= MAX_BYTES:
                return
        except OSError:
            return
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        if len(lines) <= MAX_LINES_KEPT:
            return
        kept = lines[-MAX_LINES_KEPT:]
        tmp = p.with_name(p.name + ".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(kept) + ("\n" if kept else ""))
        os.replace(str(tmp), str(p))

    # ── 읽기 ─────────────────────────────────────────────────────────────
    def _read_events(self) -> list[dict]:
        p = self._path
        if not p.is_file():
            return []
        out = []
        try:
            with p.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue  # 데몬이 쓰는 도중 잘린 줄 — 다음 줄부터 계속
                    if not isinstance(row, dict):
                        continue
                    model = row.get("model")
                    ts = row.get("ts")
                    if not isinstance(model, str) or not model.strip():
                        continue
                    if not isinstance(ts, (int, float)):
                        continue
                    tokens = row.get("tokens")
                    seconds = row.get("seconds")
                    out.append({
                        "ts": float(ts),
                        "model": model.strip(),
                        "tokens": tokens if isinstance(tokens, (int, float)) and not isinstance(tokens, bool) else 0,
                        "seconds": seconds if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) else 0,
                    })
        except OSError:
            return []
        return out

    def capability(self) -> dict:
        if not self._path.is_file():
            return {"available": False, "provider": self.name, "models": 0, "reason": "no-feed"}
        events = self._read_events()
        models = {e["model"] for e in events}
        return {"available": True, "provider": self.name, "models": len(models)}

    def snapshot(self, since: float = 0) -> Optional[dict]:
        if not self._path.is_file():
            return None
        events = self._read_events()
        if not events:
            return {"provider": self.name, "generated_at": _iso_now(), "counters": []}

        now = time.time()
        spark_since = now - SPARKLINE_DAYS * 86400

        by_model: dict[str, list[dict]] = {}
        for e in events:
            by_model.setdefault(e["model"], []).append(e)

        counters = []
        for model, evs in sorted(by_model.items()):
            windowed = [e for e in evs if e["ts"] >= since] if since else evs
            tokens = sum(e["tokens"] for e in windowed)
            seconds = sum(e["seconds"] for e in windowed)
            tok_per_sec = (tokens / seconds) if seconds > 0 else None

            # 7일 일별 스파크라인 — since와 무관하게 고정 창(화면 2e).
            buckets: dict[str, int] = {}
            for e in evs:
                if e["ts"] < spark_since:
                    continue
                buckets[_day(e["ts"])] = buckets.get(_day(e["ts"]), 0) + e["tokens"]
            samples = []
            for i in range(SPARKLINE_DAYS - 1, -1, -1):
                day = _day(now - i * 86400)
                samples.append({"day": day, "tokens": buckets.get(day, 0)})

            counters.append({
                "label": model,
                "tokens": tokens,
                "seconds": round(seconds, 1),
                "tok_per_sec": round(tok_per_sec, 1) if tok_per_sec is not None else None,
                "samples": samples,
            })

        return {"provider": self.name, "generated_at": _iso_now(), "counters": counters}


def _iso_now() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


# --- CLI (bin/fsh가 서버 없이 직접 호출한다 — queue_store.py와 같은 방식) --------


def _cli(argv: list[str]) -> int:
    import sys

    cmd = argv[0] if argv else "list"
    rest = argv[1:]

    if cmd == "add":
        opts = {}
        i = 0
        while i < len(rest):
            if rest[i] in ("--model", "--tokens", "--seconds") and i + 1 < len(rest):
                opts[rest[i][2:]] = rest[i + 1]
                i += 2
            else:
                i += 1
        if "model" not in opts or "tokens" not in opts or "seconds" not in opts:
            print("  ✗ 사용법: fsh usage add --model <이름> --tokens <N> --seconds <N>", file=sys.stderr)
            return 2
        r = CounterJsonlProvider().add(opts["model"], opts["tokens"], opts["seconds"])
        if not r.get("ok"):
            print(f"  ✗ {r['reason']}", file=sys.stderr)
            return 1
        ev = r["event"]
        print(f"  ✓ 기록됨 — {ev['model']} · {ev['tokens']} tok · {ev['seconds']}s")
        return 0

    if cmd == "list":
        snap = CounterJsonlProvider().snapshot()
        counters = (snap or {}).get("counters", [])
        print()
        if not counters:
            print("  누적 사용량 기록이 없습니다")
            print()
            print("  기록: fsh usage add --model qwen2.5-coder --tokens 1840 --seconds 41")
        else:
            print(f"  📈 누적형 사용량 — {len(counters)}개 모델")
            print()
            for c in counters:
                tps = f"{c['tok_per_sec']} tok/s" if c["tok_per_sec"] is not None else "tok/s 없음"
                print(f"    {c['label']:<20} {c['tokens']:>10} tok  {c['seconds']:>8.1f}s  {tps}")
        print()
        return 0

    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(_cli(sys.argv[1:]))
