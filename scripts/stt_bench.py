#!/usr/bin/env python3
"""STT 엔진 벤치마크 — 같은 한국어 음성으로 엔진별 정확도(CER)·속도(RTF)·메모리를 잰다.

왜 있나: 엔진을 교체 가능하게 만들기 전에, 어떤 엔진이 코딩 지시 받아쓰기에 실제로 쓸만한지
**같은 조건으로** 재야 한다. 공개 수치는 평가셋이 제각각이라 비교가 안 된다.

사용:
  stt_bench.py make-samples DIR            # `say` 한국어 음성으로 합성 샘플 + 정답 텍스트 생성
  stt_bench.py list                        # 어댑터 파일 + 실사용 모델(model:<카탈로그 id>) 목록
  stt_bench.py run ENGINE --samples DIR    # 한 엔진을 이 프로세스에서 측정(JSON 출력) — 보통 report가 부른다
  stt_bench.py report ENGINE... --samples DIR [--python PY] [--hf-home DIR]
        # 엔진마다 **별도 프로세스**로 run을 돌려 메모리(RSS)를 서로 섞지 않고 표로 집계

샘플 규약: DIR/NAME.wav (16kHz mono) + DIR/NAME.txt (정답). 직접 녹음한 파일도 같은 이름 규칙으로 넣으면 된다.
엔진 규약: **실사용 모델은 `model:<카탈로그 id>`로 부른다**(예: `model:qwen3-asr-0.6b-mlx`) — 앱이 쓰는 엔진 코드
  (server/stt_engines)를 그대로 호출하므로 측정과 실사용이 갈라지지 않는다. 모델 파일은 미리 받아 둬야 한다
  (설정에서 받거나 격리 캐시 HF_HOME에 받아 둔다 — 이 도구는 몰래 받지 않는다).
  후보 엔진 비교용 어댑터가 필요하면 scripts/stt_bench_engines/NAME.py 에 `load() -> transcribe`(`transcribe(wav_path) -> str`)를 둔다.

⚠ 한계: `say` 합성 음성은 실제 사람 음성보다 깨끗하고 일정하다. 정확도 숫자는 엔진 **순위**의 참고일 뿐
   절대값이 아니다. 실제 사용 문장을 직접 녹음해 같은 폴더에 넣으면 그쪽이 훨씬 믿을 만하다.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import resource
import subprocess
import sys
import time
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENGINES_DIR = HERE / "stt_bench_engines"

# 코딩 에이전트에 말로 지시하는 문장. 기술 용어는 한글 표기로 적는다 — 정답과 인식 결과의 철자가
# 엔진마다 갈리는(Git/깃) 문제를 CER에서 빼기 위해서다. 영어 철자 혼용은 별도 약점으로 따로 봐야 한다.
SENTENCES = [
    "파이썬 서버를 재시작하고 테스트가 통과하는지 확인해줘",
    "방금 수정한 함수에 에러 처리를 추가해줘",
    "깃 커밋 메시지를 한국어로 작성해서 푸시해줘",
    "이 파일에서 사용하지 않는 임포트를 전부 지워줘",
    "도커 컨테이너가 왜 계속 죽는지 로그를 확인해봐",
    "로그인 화면의 버튼 색상을 파란색으로 바꿔줘",
    "데이터베이스 마이그레이션 파일을 새로 만들어줘",
    "테스트 코드가 실패한 이유를 찾아서 고쳐줘",
    "이 함수가 어디서 호출되는지 전부 찾아줘",
    "브랜치를 새로 만들어서 작업을 옮겨줘",
    "에이피아이 응답 시간이 느린 원인을 분석해줘",
    "설정 파일에서 포트 번호를 팔천으로 변경해줘",
    "방금 만든 변경사항을 되돌려줘",
    "리드미 문서에 설치 방법을 추가해줘",
    "타입 오류가 나는 부분을 모두 수정해줘",
    "터미널에서 이 명령어를 실행하고 결과를 알려줘",
    "서버가 켜져 있는지 상태를 확인해줘",
    "프론트엔드 빌드가 성공하는지 확인해줘",
    "이 코드를 더 간단하게 리팩터링해줘",
    "작업이 끝나면 요약해서 알려줘",
]
# 이 맥에서 실제로 소리가 나는 한국어 `say` 음성은 Yuna 하나뿐이다(Eddy·Flo·Reed·Sandy 등은 빈 파일을
# 만든다 — 실측 2026-10-02, 첫 벤치가 무음 15개로 오염된 원인). 변주는 말하기 속도로 준다.
VARIANTS = [("Yuna", 150), ("Yuna", 180), ("Yuna", 210), ("Yuna", 240)]
MIN_SECONDS = 0.5     # 이보다 짧은 샘플은 무음/실패로 본다


def _norm(s: str) -> str:
    """CER용 정규화 — 공백·구두점·대소문자를 버리고 한글/영숫자만 비교."""
    return re.sub(r"[^0-9a-z가-힣]", "", s.lower())


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(ref: str, hyp: str) -> float:
    r, h = _norm(ref), _norm(hyp)
    if not r:
        return 0.0
    return _edit_distance(r, h) / len(r)


def _wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def cmd_make_samples(args) -> int:
    out = Path(args.dir)
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    for i, text in enumerate(SENTENCES):
        voice, rate = VARIANTS[i % len(VARIANTS)]
        stem = f"s{i:02d}_{voice.lower()}{rate}"
        aiff = out / f"{stem}.aiff"
        wav = out / f"{stem}.wav"
        subprocess.run(["say", "-v", voice, "-r", str(rate), "-o", str(aiff), text], check=True)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(wav)], check=True)
        aiff.unlink()
        _check_audible(wav)
        (out / f"{stem}.txt").write_text(text + "\n", encoding="utf-8")
        n += 1
    print(f"{n}개 샘플 생성: {out}")
    return 0


def _check_audible(wav: Path) -> None:
    """샘플이 비었거나 무음이면 즉시 실패 — 무음 샘플이 CER을 조용히 오염시킨다(실제로 그랬다)."""
    with wave.open(str(wav), "rb") as w:
        frames = w.readframes(w.getnframes())
        secs = w.getnframes() / w.getframerate()
        width = w.getsampwidth()
    peak = max((abs(int.from_bytes(frames[i:i + width], "little", signed=True))
                for i in range(0, len(frames) - width + 1, width * 16)), default=0)
    if secs < MIN_SECONDS or peak < 200:
        raise SystemExit(f"무음/빈 샘플: {wav.name} ({secs:.2f}초, 최대 진폭 {peak}) — 음성이 설치돼 있는지 확인")


def _samples(d: Path):
    for wav in sorted(d.glob("*.wav")):
        txt = wav.with_suffix(".txt")
        if txt.exists():
            yield wav, txt.read_text(encoding="utf-8").strip()


class _ProdAdapter:
    """실사용 엔진(server/stt_engines)을 어댑터 규약에 맞춘 얇은 껍데기."""

    def __init__(self, model_id: str):
        sys.path.insert(0, str(HERE.parent / "server"))
        from stt_engines import catalog, get_engine
        self._spec = catalog.get(model_id)
        if self._spec is None:
            raise SystemExit(f"카탈로그에 없는 모델: {model_id}")
        self._eng = get_engine(self._spec.engine)
        self.INFO = f"{self._spec.label} [{self._spec.engine}]"

    def load(self):
        self._eng.load(self._spec)     # 없으면 NotReady — 몰래 받지 않는다

        def transcribe(path):
            with open(path, "rb") as f:
                return self._eng.transcribe(f.read(), "ko")
        return transcribe

    def peak_rss_mb(self):
        return self._eng.rss_mb()


def _load_engine(name: str):
    if name.startswith("model:"):
        return _ProdAdapter(name[len("model:"):])
    path = ENGINES_DIR / f"{name}.py"
    if not path.exists():
        raise SystemExit(f"엔진 '{name}' 없음: {path}")
    spec = importlib.util.spec_from_file_location(f"stt_bench_engine_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def cmd_run(args) -> int:
    d = Path(args.samples)
    samples = list(_samples(d))
    if not samples:
        raise SystemExit(f"샘플 없음: {d}")
    for wav, _ in samples:
        _check_audible(wav)          # 오염된 샘플로 숫자를 내지 않는다
    mod = _load_engine(args.engine)
    t0 = time.perf_counter()
    transcribe = mod.load()
    load_sec = time.perf_counter() - t0
    # 첫 호출은 JIT·캐시 워밍업이라 측정에서 뺀다(사용자는 모델을 올려 둔 채 쓴다).
    transcribe(str(samples[0][0]))
    rows, total_audio, total_proc = [], 0.0, 0.0
    for wav, ref in samples:
        dur = _wav_seconds(wav)
        t = time.perf_counter()
        try:
            hyp = transcribe(str(wav)) or ""
            err = None
        except Exception as e:                        # 한 파일 실패가 전체를 막지 않게
            hyp, err = "", f"{type(e).__name__}: {e}"[:200]
        proc = time.perf_counter() - t
        total_audio += dur
        total_proc += proc
        rows.append({"file": wav.name, "ref": ref, "hyp": hyp, "cer": round(cer(ref, hyp), 4),
                     "sec": round(proc, 3), "audio_sec": round(dur, 2), **({"error": err} if err else {})})
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # macOS: bytes
    extra_mb = 0
    if hasattr(mod, "peak_rss_mb"):  # 별도 상주 프로세스(예: Swift CLI)의 메모리는 어댑터가 알려준다
        try:
            extra_mb = int(mod.peak_rss_mb())
        except Exception:
            extra_mb = 0
    result = {
        "engine": args.engine,
        "info": getattr(mod, "INFO", ""),
        "load_sec": round(load_sec, 2),
        "peak_rss_mb": round(peak / (1024 * 1024)) + extra_mb,   # 이 프로세스 + 어댑터가 알려준 별도 프로세스
        "rtf": round(total_proc / total_audio, 3) if total_audio else None,
        "avg_sec": round(total_proc / len(rows), 3),
        "cer_mean": round(sum(r["cer"] for r in rows) / len(rows), 4),
        "errors": sum(1 for r in rows if "error" in r),
        "rows": rows,
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0


def cmd_report(args) -> int:
    results, failed = [], []
    for eng in args.engines:
        env = dict(os.environ)
        if args.hf_home:
            env["HF_HOME"] = args.hf_home
        cmd = [args.python, str(Path(__file__).resolve()), "run", eng, "--samples", args.samples]
        print(f"▶ {eng} …", file=sys.stderr, flush=True)
        p = subprocess.run(cmd, env=env, capture_output=True, text=True)
        line = next((l for l in reversed(p.stdout.splitlines()) if l.startswith("{")), "")
        if p.returncode != 0 or not line:
            failed.append((eng, (p.stderr or p.stdout).strip().splitlines()[-1:] or ["?"]))
            continue
        results.append(json.loads(line))
    if args.json:
        Path(args.json).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{'엔진':<28}{'CER%':>7}{'RTF':>8}{'평균s':>8}{'로드s':>8}{'RSS MB':>9}{'오류':>5}")
    for r in sorted(results, key=lambda x: x["cer_mean"]):
        print(f"{r['engine']:<28}{r['cer_mean']*100:>7.1f}{r['rtf']:>8.3f}{r['avg_sec']:>8.2f}"
              f"{r['load_sec']:>8.1f}{r['peak_rss_mb']:>9}{r['errors']:>5}")
    for eng, why in failed:
        print(f"{eng:<28}실패: {why[0][:90]}")
    if args.show_errors:
        for r in results:
            bad = [x for x in r["rows"] if x["cer"] > 0]
            print(f"\n[{r['engine']}] 틀린 문장 {len(bad)}/{len(r['rows'])}")
            for x in bad[: args.show_errors]:
                print(f"  정답: {x['ref']}\n  인식: {x['hyp']}  (CER {x['cer']*100:.0f}%)")
    return 0


def cmd_list(_args) -> int:
    sys.path.insert(0, str(HERE.parent / "server"))
    from stt_engines import catalog
    for m in catalog.CATALOG:
        print(f"model:{m.id}")
    for p in sorted(ENGINES_DIR.glob("*.py")):
        if not p.name.startswith("_"):
            print(p.stem)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("make-samples"); a.add_argument("dir"); a.set_defaults(fn=cmd_make_samples)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    r = sub.add_parser("run"); r.add_argument("engine"); r.add_argument("--samples", required=True); r.set_defaults(fn=cmd_run)
    p = sub.add_parser("report"); p.add_argument("engines", nargs="+"); p.add_argument("--samples", required=True)
    p.add_argument("--python", default=sys.executable); p.add_argument("--hf-home")
    p.add_argument("--json"); p.add_argument("--show-errors", type=int, default=0)
    p.set_defaults(fn=cmd_report)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
