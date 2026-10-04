# STT 벤치마크 어댑터

앱이 쓰는 모델은 어댑터 없이 `stt_bench.py report model:<카탈로그 id> ...`로 잰다(실사용 엔진 코드를 그대로 호출).

**후보 엔진을 비교**할 때만 여기에 파일을 둔다: `NAME.py`에 `INFO`(문자열)와 `load() -> transcribe`,
`transcribe(wav_path) -> str`을 만들고, 별도 임시 venv에서 `stt_bench.py report NAME --python <그 venv python>`로 돌린다.
2026-10-02 비교에 쓴 어댑터(faster-whisper, mlx-whisper, sherpa-onnx, Moonshine, SenseVoice 등)는 결과를
`docs/ref/voice.md`에 남기고 지웠다 — 필요하면 git 이력에서 되살린다.
