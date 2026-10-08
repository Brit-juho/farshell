"""STT 모델 카탈로그 — 엔진·크기·특징·측정치·지원 환경.

측정치(`bench`)는 scripts/stt_bench.py로 잰 값이다: **합성 음성(`say` Yuna) 20문장, Apple Silicon, 2026-10-02**.
실제 사람 목소리·x86 CPU에서는 다를 수 있다 — UI가 `bench.basis`를 같이 보여준다. 측정 안 한 항목은 None.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .env import HostEnv

BASIS = "합성 음성 20문장 · Apple Silicon · 2026-10-02"


@dataclass(frozen=True)
class ModelSpec:
    id: str
    engine: str                      # stt_engines 레지스트리의 엔진 id
    label: str
    tagline: str                     # 한 줄 특징
    repo: str = ""                   # HuggingFace 리포(해당 시)
    files: tuple = ()                # 받을 파일(비면 리포 전체 아님 — 엔진이 정한다)
    size_mb: int = 0                 # 디스크 용량 어림(MB). 0이면 시스템이 관리
    static_tags: tuple = ()          # "고정확" | "경량" | "실험적"
    bench: Optional[dict] = None     # {"cer": 6.1, "sec": 0.10, "rss_mb": 1160, "basis": ...}
    license: str = ""
    notes: tuple = ()                # 사용자에게 보여줄 주의 문구들
    custom: bool = False

    def to_dict(self) -> dict:
        return {"id": self.id, "engine": self.engine, "label": self.label, "tagline": self.tagline,
                "repo": self.repo, "size_mb": self.size_mb, "static_tags": list(self.static_tags),
                "bench": self.bench, "license": self.license, "notes": list(self.notes),
                "custom": self.custom}


def _bench(cer, sec, rss_mb):
    return {"cer": cer, "sec": sec, "rss_mb": rss_mb, "basis": BASIS}


CATALOG: tuple = (
    ModelSpec(
        id="qwen3-asr-0.6b-mlx", engine="mlx_qwen3", label="Qwen3-ASR 0.6B (MLX)",
        tagline="영어·한글이 섞인 코딩 지시에 안정적. 애플 칩 GPU로 가장 빠르게 도는 균형형",
        repo="mlx-community/Qwen3-ASR-0.6B-8bit", size_mb=971,
        bench=_bench(6.1, 0.10, 1160), license="Apache-2.0",
        notes=("양자화는 8bit — 4bit는 한국어에서 오류가 크게 늘어난다는 보고가 있다",),
    ),
    ModelSpec(
        id="qwen3-asr-1.7b-mlx", engine="mlx_qwen3", label="Qwen3-ASR 1.7B (MLX)",
        tagline="정확도가 가장 높다. 메모리 약 2.5GB를 쓴다",
        repo="mlx-community/Qwen3-ASR-1.7B-8bit", size_mb=2300, static_tags=("고정확",),
        bench=_bench(3.0, 0.19, 2550), license="Apache-2.0",
    ),
    ModelSpec(
        id="qwen3-asr-0.6b-gguf", engine="llamacpp_qwen3", label="Qwen3-ASR 0.6B (llama.cpp)",
        tagline="맥·Linux·WSL2 어디서나 돈다. GPU 없이 CPU만으로도 동작",
        repo="ggml-org/Qwen3-ASR-0.6B-GGUF",
        files=("Qwen3-ASR-0.6B-Q8_0.gguf", "mmproj-Qwen3-ASR-0.6B-Q8_0.gguf"), size_mb=1019,
        static_tags=("실험적",), bench=_bench(6.1, 0.25, 2000), license="Apache-2.0",
        notes=("llama.cpp의 오디오 입력은 아직 실험 단계라 품질이 달라질 수 있다",
               "측정은 Apple Silicon CPU 기준 — x86 PC의 속도는 확인하지 못했다",
               "메모리는 약 2GB(별도 프로세스라 내리면 OS로 돌려준다)"),
    ),
    ModelSpec(
        id="qwen3-asr-1.7b-gguf", engine="llamacpp_qwen3", label="Qwen3-ASR 1.7B (llama.cpp)",
        tagline="큰 모델의 높은 정확도를 모든 플랫폼에서. 메모리·용량이 크다",
        repo="ggml-org/Qwen3-ASR-1.7B-GGUF",
        files=("Qwen3-ASR-1.7B-Q8_0.gguf", "mmproj-Qwen3-ASR-1.7B-Q8_0.gguf"), size_mb=2406,
        static_tags=("고정확", "실험적"), bench=None, license="Apache-2.0",
        notes=("아직 이 환경에서 측정하지 않았다", "llama.cpp의 오디오 입력은 아직 실험 단계"),
    ),
    ModelSpec(
        id="apple-speech-ko", engine="apple_speech", label="Apple 음성 인식 (SpeechAnalyzer)",
        tagline="맥에 내장된 기능. 한국어 시스템 자산을 준비해 쓰는 경량 엔진",
        size_mb=0, static_tags=("경량",), bench=_bench(6.4, 0.06, 36), license="OS 기능",
        notes=("숫자는 숫자로 적는다(팔천 → 8000)", "일부 단어를 오인식할 수 있다(깃 → 비커밍)",
               "메모리 수치에는 시스템 서비스가 쓰는 몫이 빠져 있다"),
    ),
)

_BY_ID = {m.id: m for m in CATALOG}


def get(model_id: str) -> Optional[ModelSpec]:
    return _BY_ID.get(model_id)


# 환경별 「권장」 — **한 환경에 하나**. 근거는 실측(docs/ref/voice.md의 벤치마크 표).
def recommended_id(env: HostEnv) -> str:
    if env.os == "macos" and env.arch == "arm64":
        return "qwen3-asr-0.6b-mlx"
    return "qwen3-asr-0.6b-gguf"
