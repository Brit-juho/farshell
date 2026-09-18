"""STT 모델 캐시 조회/삭제 — voice_handler.stt_model_info / delete_stt_model."""

import asyncio

import pytest

import voice_handler
from routes import voice as voice_routes


def _fake_cache(tmp_path):
    """whisper 리포 하나 + 무관한 리포 하나를 흉내 낸 HF 캐시 디렉터리."""
    cache = tmp_path / "hub"
    whisper_dir = cache / "models--Systran--faster-whisper-base"
    whisper_dir.mkdir(parents=True)
    (whisper_dir / "model.bin").write_bytes(b"x" * 1000)
    other_dir = cache / "models--org--some-llm"
    other_dir.mkdir(parents=True)
    (other_dir / "weights.bin").write_bytes(b"y" * 1000)
    return cache


def test_stt_model_info_only_lists_whisper_repos(tmp_path, monkeypatch):
    cache = _fake_cache(tmp_path)
    monkeypatch.setattr(voice_handler, "_hf_cache_dir", lambda: cache)

    models = voice_handler.stt_model_info()

    assert len(models) == 1
    assert models[0]["name"] == "Systran/faster-whisper-base"
    assert models[0]["size_bytes"] == 1000


def test_stt_model_info_missing_cache_dir_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(voice_handler, "_hf_cache_dir", lambda: tmp_path / "no-such-dir")

    assert voice_handler.stt_model_info() == []


def test_delete_stt_model_removes_folder(tmp_path, monkeypatch):
    cache = _fake_cache(tmp_path)
    monkeypatch.setattr(voice_handler, "_hf_cache_dir", lambda: cache)
    target = cache / "models--Systran--faster-whisper-base"

    voice_handler.delete_stt_model(str(target))

    assert not target.exists()


def test_delete_stt_model_rejects_path_outside_listed_models(tmp_path, monkeypatch):
    cache = _fake_cache(tmp_path)
    monkeypatch.setattr(voice_handler, "_hf_cache_dir", lambda: cache)
    outside = tmp_path / "hub" / "models--org--some-llm"  # whisper 아님 → 목록에 없음

    with pytest.raises(ValueError):
        voice_handler.delete_stt_model(str(outside))
    assert outside.exists()  # 지워지지 않았다


def test_route_delete_rejects_unlisted_path(monkeypatch):
    monkeypatch.setattr(voice_handler, "stt_model_info", lambda: [])

    class _Req:
        async def json(self):
            return {"path": "/tmp/not-a-real-model"}

    resp = asyncio.run(voice_routes.stt_model_delete(_Req()))

    assert resp.status_code == 400


def test_route_delete_requires_path(monkeypatch):
    class _Req:
        async def json(self):
            return {}

    resp = asyncio.run(voice_routes.stt_model_delete(_Req()))

    assert resp.status_code == 400
