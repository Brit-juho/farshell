"""STT 서비스 — 어떤 모델을 쓸지 정하고(선택 해석), 엔진을 올리고 내리고, 받아쓰고, 화면용 카탈로그를 만든다.

voice_handler(오디오 디코딩)와 routes/voice.py(REST) 사이의 한 층이다. 엔진 자체는 stt_engines/ 에 있다.

원칙:
- **STT는 이 서버가 도는 OS에서만 실행된다.** 접속하는 폰·PC는 녹음만 보낸다.
- **몰래 받지 않는다.** 여기의 어떤 경로도(load/preload/transcribe) 네트워크로 받지 않는다. 받기는 `start_setup`
  (사용자가 확인 창에서 승인한 요청)에서만.
- 한 번에 한 요청만 엔진을 쓴다(직렬화). 줄 서 있는 요청 수에 상한을 둔다.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Optional

import stt_engines
from stt_engines import catalog, hfcache, llama_release, setup_jobs
from stt_engines.base import NotReady
from stt_engines.catalog import ModelSpec
from stt_engines.env import HostEnv, detect

logger = logging.getLogger(__name__)

SETTING_KEY = "voice.sttModel"
MAX_WAITING = 4          # 엔진을 쓰는 요청 + 줄 선 요청의 상한 — 넘으면 Busy
_CUSTOM_RE = re.compile(
    r"^custom:(mlx_qwen3|llamacpp_qwen3):([A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95})$")

_setup = setup_jobs.SetupManager()
_run_lock = threading.Lock()          # 엔진 load/unload/transcribe 직렬화
_state_lock = threading.Lock()        # 아래 카운터·타임스탬프 보호
_waiting = 0
_last_use = 0.0
_last_error = ""
_last_timing: Optional[dict] = None


class Busy(RuntimeError):
    """줄 선 요청이 너무 많다."""


# ── 선택 해석 ───────────────────────────────────────────────────────

def parse_custom(model_id: str) -> Optional[ModelSpec]:
    m = _CUSTOM_RE.match(model_id or "")
    if not m or ".." in model_id:
        return None
    engine, repo = m.group(1), m.group(2)
    return ModelSpec(id=model_id, engine=engine, label=repo, tagline="직접 입력한 모델", repo=repo,
                     custom=True, notes=("직접 입력한 모델 — 이 앱이 검증하지 않았습니다",))


def spec_for(model_id: str) -> Optional[ModelSpec]:
    return catalog.get(model_id) or parse_custom(model_id)


@dataclass
class Selection:
    model_id: Optional[str]
    spec: Optional[ModelSpec]
    valid: bool
    legacy: Optional[str]
    is_default: bool


def _raw_setting() -> str:
    raw = os.environ.get("VT_STT_MODEL", "").strip()
    if not raw:
        try:
            import workspace
            raw = str((workspace.load().get("settings") or {}).get(SETTING_KEY, "")).strip()
        except Exception:
            raw = ""
    return raw[:200]


def selection(env: Optional[HostEnv] = None) -> Selection:
    env = env or detect()
    raw = _raw_setting()
    if not raw:
        rec = catalog.recommended_id(env)
        return Selection(rec, catalog.get(rec), True, None, True)
    spec = spec_for(raw)
    if spec is None:
        return Selection(None, None, False, raw, False)
    ok, _ = stt_engines.get_engine(spec.engine).supported(env)
    if not ok:
        return Selection(None, None, False, raw, False)
    return Selection(spec.id, spec, True, None, False)


def _invalid_message(sel: Selection) -> str:
    return (f"이전에 고른 모델('{sel.legacy}')은 이 서버에서 더 이상 쓸 수 없습니다 — "
            "설정 「음성 및 알림」에서 모델을 다시 골라 주세요")


# ── 엔진 구동 ───────────────────────────────────────────────────────

def _loaded_engine():
    """지금 모델을 올려 둔 엔진(없으면 None). 인스턴스를 새로 만들지 않는다."""
    for inst in list(stt_engines._instances.values()):
        if inst.loaded():
            return inst
    return None


def _ensure_loaded_locked() -> tuple:
    sel = selection()
    if not sel.valid or sel.spec is None:
        raise NotReady(_invalid_message(sel))
    spec = sel.spec
    eng = stt_engines.get_engine(spec.engine)
    for eid, inst in list(stt_engines._instances.items()):
        if eid != spec.engine and inst.loaded():
            inst.unload()                      # 한 번에 한 엔진만 메모리에 둔다
    if eng.loaded() != spec.id:
        eng.load(spec)
    return eng, spec


def preload() -> Optional[str]:
    """선택된 모델을 올린다. 실패하면 이유를 last_error에 남기고 None."""
    global _last_error
    _warm_decoder()
    try:
        with _run_lock:
            eng, spec = _ensure_loaded_locked()
        _touch()
        _last_error = ""
        return eng.id
    except NotReady as e:
        _last_error = str(e)
        return None
    except Exception as e:
        _last_error = f"{type(e).__name__}: {e}"[:300]
        logger.warning("STT preload 실패: %s", e)
        return None


def _warm_decoder() -> None:
    """오디오 디코더(pyav)를 미리 불러 둔다 — 새로 설치한 환경에서 첫 import가 수 초~십수 초 걸려(macOS가 새
    라이브러리를 검사한다) 첫 음성 입력의 지연이 됐다. 없으면(ffmpeg 폴백) 조용히 넘어간다."""
    try:
        import av  # noqa: F401
    except Exception:
        pass


def unload() -> bool:
    with _run_lock:
        was = _loaded_engine() is not None
        for inst in list(stt_engines._instances.values()):
            inst.unload()
        return was


def loaded() -> bool:
    return _loaded_engine() is not None


def loaded_model_id() -> Optional[str]:
    e = _loaded_engine()
    return e.loaded() if e else None


def loaded_engine_id() -> Optional[str]:
    e = _loaded_engine()
    return e.id if e else None


def rss_mb() -> int:
    e = _loaded_engine()
    return e.rss_mb() if e else 0


def available(env: Optional[HostEnv] = None) -> bool:
    """이 서버에서 STT를 쓸 수 있는 엔진이 하나라도 있는가(모델·패키지 준비 여부와 무관 — 설정에서 준비한다)."""
    env = env or detect()
    for eid in stt_engines.engine_ids():
        try:
            if stt_engines.get_engine(eid).supported(env)[0]:
                return True
        except Exception:
            continue
    return False


def last_error() -> str:
    return _last_error


def last_timing() -> Optional[dict]:
    return _last_timing


def _touch() -> None:
    global _last_use
    with _state_lock:
        _last_use = time.monotonic()


def transcribe_wav(wav: bytes, language: Optional[str]) -> tuple:
    """블로킹(스레드에서 부른다). (텍스트, {"wait_ms","load_ms","stt_ms"}). 못 쓰면 NotReady, 줄이 길면 Busy."""
    global _waiting, _last_error, _last_timing
    with _state_lock:
        if _waiting >= MAX_WAITING:
            raise Busy("음성 인식이 밀려 있습니다 — 잠시 뒤에 다시 말해 주세요")
        _waiting += 1
    t0 = time.perf_counter()
    try:
        with _run_lock:
            t1 = time.perf_counter()
            eng, spec = _ensure_loaded_locked()
            t2 = time.perf_counter()
            text = eng.transcribe(wav, language)
            t3 = time.perf_counter()
    except NotReady as e:
        _last_error = str(e)
        raise
    finally:
        with _state_lock:
            _waiting -= 1
    _touch()
    _last_error = ""
    timing = {"wait_ms": round((t1 - t0) * 1000), "load_ms": round((t2 - t1) * 1000),
              "stt_ms": round((t3 - t2) * 1000)}
    _last_timing = timing
    return text, timing


async def idle_monitor() -> None:
    """VT_STT_IDLE_SEC(기본 600초) 동안 안 쓰면 모델을 내려 메모리를 돌려준다. 0이면 끈다."""
    try:
        idle = float(os.environ.get("VT_STT_IDLE_SEC", "600"))
    except ValueError:
        idle = 600.0
    if idle <= 0:
        return
    import asyncio
    while True:
        await asyncio.sleep(30)
        if loaded() and _last_use > 0 and time.monotonic() - _last_use > idle:
            await asyncio.get_running_loop().run_in_executor(None, unload)
            logger.info("STT 모델 유휴 %ss — 내렸습니다", int(idle))


# ── 카탈로그 응답 ───────────────────────────────────────────────────

def _model_view(spec: ModelSpec, env: HostEnv, sel: Selection, rec_id: str) -> dict:
    eng = stt_engines.get_engine(spec.engine)
    ok, reason = eng.supported(env)
    reqs = [r.to_dict() for r in eng.requirements(env)] if ok else []
    present = bool(ok and eng.model_present(spec))
    tags = list(spec.static_tags)
    if spec.id == rec_id:
        tags.insert(0, "권장")
    d = spec.to_dict()
    d.update({
        "engine_label": eng.label, "tags": tags, "supported": ok, "unsupported_reason": "" if ok else reason,
        "requirements": reqs, "model_present": present,
        "ready": bool(ok and present and all(r["satisfied"] for r in reqs)),
        "selected": sel.model_id == spec.id, "loaded": loaded_model_id() == spec.id,
    })
    d.pop("static_tags", None)
    return d


def _engine_views(env: HostEnv) -> list:
    out = []
    for eid in stt_engines.engine_ids():
        eng = stt_engines.get_engine(eid)
        ok, reason = eng.supported(env)
        out.append({"id": eid, "label": eng.label, "usable": ok, "reason": "" if ok else reason})
    return out


def catalog_payload(custom: Optional[str] = None) -> dict:
    env = detect()
    sel = selection(env)
    rec = catalog.recommended_id(env)
    models, unsupported = [], []
    for spec in catalog.CATALOG:
        v = _model_view(spec, env, sel, rec)
        (models if v["supported"] else unsupported).append(v)
    models.sort(key=lambda m: (m["id"] != rec, ))             # 권장을 맨 앞(나머지는 카탈로그 순서 유지: 안정 정렬)
    shown = {m["id"] for m in models}
    extra = [sel.spec] if sel.spec is not None and sel.spec.custom else []
    cspec = parse_custom(custom) if custom else None         # 사용자가 입력 중인 직접 입력 모델의 준비 상태 조회
    if cspec is not None:
        extra.append(cspec)
    for spec in extra:
        if spec.id not in shown:
            models.append(_model_view(spec, env, sel, rec))
            shown.add(spec.id)
    return {"env": env.to_dict(), "recommended": rec, "selected": sel.model_id if sel.valid else None,
            "selected_valid": sel.valid, "legacy_value": sel.legacy, "engines": _engine_views(env),
            "models": models, "unsupported": unsupported, "setup": _setup.status()}


def status_payload() -> dict:
    sel = selection()
    return {"available": available(), "loaded": loaded(), "engine": loaded_engine_id(),
            "model": sel.model_id, "selected_valid": sel.valid, "loaded_model": loaded_model_id(),
            "rss_mb": rss_mb(), "setup": _setup.status(), "last_error": _last_error or None,
            "timing": {"last": _last_timing} if _last_timing else None}


# ── 준비(받기) ──────────────────────────────────────────────────────

class SetupError(ValueError):
    """잘못된 준비 요청(400)."""


def start_setup(model_id: str, step: str, blocking: bool = False) -> dict:
    spec = spec_for(model_id)
    if spec is None:
        raise SetupError("알 수 없는 모델입니다 — 목록의 모델이거나 custom:<엔진>:<owner/repo> 형식이어야 합니다")
    env = detect()
    eng = stt_engines.get_engine(spec.engine)
    ok, reason = eng.supported(env)
    if not ok:
        raise SetupError(reason)
    if step == "model":
        label = f"{spec.label} 모델 받기"

        def fn(progress):
            eng.download_model(spec, progress)
            if spec.custom:
                _record_custom(spec.id)
        if eng.model_present(spec):
            return _done_job(model_id, "model", label)
    elif step.startswith("req:"):
        req_id = step[4:]
        req = next((r for r in eng.requirements(env) if r.id == req_id), None)
        if req is None:
            raise SetupError(f"이 엔진에 없는 항목입니다: {req_id}")
        if req.satisfied:
            return _done_job(model_id, step, req.label)
        if not req.can_install:
            raise SetupError(f"앱이 대신 설치하지 않는 항목입니다 — {req.hint or '안내된 방법으로 직접 설치해 주세요'}")
        label = f"{req.label} 설치"

        def fn(progress):
            eng.install_requirement(req_id, progress)
    else:
        raise SetupError("step은 'model' 또는 'req:<항목 id>'여야 합니다")
    try:
        return _setup.start(model_id, step, label, fn, blocking=blocking)
    except setup_jobs.SetupBusy:
        raise


def _done_job(model_id: str, step: str, label: str) -> dict:
    return {"id": "noop", "model": model_id, "state": "done", "error": None,
            "steps": [{"id": step, "label": label, "state": "done", "done": 0, "total": None}]}


# ── 받아 둔 항목 관리 ───────────────────────────────────────────────

def _custom_manifest():
    import vt_paths
    return vt_paths.state_dir() / "engines" / "custom_models.json"


def _read_custom() -> list:
    try:
        data = json.loads(_custom_manifest().read_text(encoding="utf-8"))
        return [m for m in data.get("models", []) if isinstance(m, str) and parse_custom(m)]
    except Exception:
        return []


def _record_custom(model_id: str) -> None:
    ids = _read_custom()
    if model_id in ids:
        return
    ids.append(model_id)
    p = _custom_manifest()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"models": ids}), encoding="utf-8")
        os.replace(tmp, p)
    except OSError as e:
        logger.warning("직접 입력 모델 기록 실패(목록에 안 보일 수 있음): %s", e)


def _model_bytes(spec: ModelSpec, eng) -> int:
    if spec.files:
        total = 0
        for n in spec.files:
            p = hfcache.cached_file(spec.repo, n)
            if p is not None:
                total += p.resolve().stat().st_size
        return total
    return hfcache.dir_size(hfcache.repo_dir(spec.repo)) if spec.repo else 0


def downloads_payload() -> dict:
    env = detect()
    sel = selection(env)
    out_models = []
    specs = list(catalog.CATALOG) + [s for s in map(parse_custom, _read_custom()) if s]
    if sel.spec is not None and sel.spec.custom and all(s.id != sel.spec.id for s in specs):
        specs.append(sel.spec)
    for spec in specs:
        eng = stt_engines.get_engine(spec.engine)
        if not eng.supported(env)[0] or not eng.model_present(spec):
            continue
        size = _model_bytes(spec, eng)
        if size <= 0:                    # 시스템이 관리하는 것(Apple)은 지울 게 없다
            continue
        out_models.append({"model_id": spec.id, "label": spec.label, "engine": spec.engine,
                           "size_bytes": size, "selected": sel.model_id == spec.id})
    binaries = []
    root = llama_release.install_root()
    if root.is_dir():
        size = hfcache.dir_size(root)
        if size > 0:
            binaries.append({"id": "llama-server", "label": "llama.cpp (앱이 받은 실행 파일)",
                             "path": str(root), "size_bytes": size})
    return {"models": out_models, "binaries": binaries}


def delete_download(model_id: Optional[str] = None, binary: Optional[str] = None) -> int:
    if (model_id is None) == (binary is None):
        raise SetupError("model_id 또는 binary 중 하나만 지정해야 합니다")
    if binary is not None:
        if binary != "llama-server":
            raise SetupError(f"알 수 없는 항목: {binary}")
        inst = stt_engines._instances.get("llamacpp_qwen3")
        if inst is not None:
            inst.unload()
        return llama_release.remove_installed()
    spec = spec_for(model_id)
    if spec is None:
        raise SetupError("알 수 없는 모델입니다")
    # 직접 입력 모델은 **이 앱이 받은 것(매니페스트)이거나 지금 선택 중인 것**만 지운다 — 아니면 사용자가 아무 리포
    # id나 적어 무관한 HuggingFace 캐시(다른 도구가 받은 모델)를 지울 수 있다.
    if spec.custom and model_id not in _read_custom() and selection().model_id != model_id:
        raise SetupError("이 앱이 받은 직접 입력 모델이 아니라서 지우지 않습니다")
    eng = stt_engines.get_engine(spec.engine)
    return eng.delete_model(spec)
