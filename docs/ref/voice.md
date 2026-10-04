# Voice, clipboard, push

> `CLAUDE.md`의 영역 색인에서 갈라져 나온 문서다(2026-09-18). 이 영역을 만지기 전에 읽는다.

## Features

| Feature | Description |
|------|------|
| Voice Daemon | macOS hotkey (Ctrl+Shift+M) → STT → direct input into tmux |
| Hands-free mode | Mobile 🔄 button → continuous record/STT loop |
| Voice-only mode | 🎧 button → hides the terminal and shows only a large mic (for earbud operation) |
| Web Push (P5) | ⋯ menu → "Push Notifications". Existing notifications (`/ws-notify` → Notification API) only work **while a PWA tab is alive**, so turning off the phone screen meant missing "waiting for approval." This fills that gap. No push is sent while at least one WS client is connected (to avoid the same notification arriving twice). **Requirements**: ① https — Service Workers don't even register over plain http ② iOS requires adding to the home screen as a PWA (16.4+; a subscription can't be created from a Safari tab — no workaround). **A subscription is bound to its origin** — if the trycloudflare URL changes, existing subscriptions all die, so each subscription stores its origin, mismatches are excluded from sending, and 404/410 responses are cleaned up on the spot. Notification bodies never contain commands, paths, or code (they'd show on the lock screen). The VAPID key is auto-generated at `~/.vt/vapid.json` (0600) — **deleting it invalidates every existing subscription**. SW registration is handled by `js/swreg.js` (it used to live inside `voice.js`, so the SW never registered at all when voice wasn't installed) |

### Voice Daemon (standalone macOS voice input)

A daemon that types voice input directly into tmux via a hotkey, without needing the server.

```bash
# Run
"$VT_PYTHON" server/voice_daemon.py &

# Usage: Ctrl+Shift+M (toggle) → speak → STT → typed into the active tmux pane
# Requires allowing the terminal app under macOS System Settings → Privacy → Accessibility
```

### Clipboard Daemon (macOS clipboard sync)

When connecting to the web terminal remotely/from mobile, the browser can only access
"that device's" clipboard, so copies made on the Mac (server) side don't automatically
carry over. Two paths cover this:

- **OSC52** (no separate process needed) — copies made inside terminal programs like
  `vim` or `tmux copy-mode` are already carried in the PTY output stream, so
  `frontend/js/terminal.js` intercepts them via
  `term.parser.registerOscHandler(52, ...)` and applies them to the clipboard of
  whatever device has the web page open.
- **Polling daemon** (`fsh clip`) — copies made outside the terminal (Safari, Finder,
  etc.) can't be caught via OSC52, so `server/clipboard_daemon.py` polls
  `NSPasteboard.changeCount` and, on a change, delivers it to the web via
  `POST /api/clipboard/push` → `/ws-notify` broadcast.

```bash
# Run (or use fsh clip)
"$VT_PYTHON" server/clipboard_daemon.py &
```


## STT 구조 (2026-10)

- **STT는 서버 OS에서만 돈다.** 브라우저는 녹음해서 `/voice/input`으로 올리기만 한다(클라이언트 쪽 인식 없음).
  그래서 쓸 수 있는 엔진은 서버 호스트가 정한다(`stt_engines/env.py`가 감지).
- `server/stt_service.py`가 선택(`voice.sttModel`, 환경변수 `VT_STT_MODEL` 우선; 비면 환경별 권장)·엔진 구동·
  직렬화·카탈로그/준비/삭제 응답을 맡고, `server/stt_engines/`가 엔진(MLX Qwen3, llama.cpp Qwen3, Apple Speech)을 구현한다.
- **몰래 받지 않는다**: `load`/`preload`/`transcribe`는 네트워크를 안 쓴다. 받기는 `POST /voice/stt/setup`
  (설정 UI가 확인 창을 거쳐 부른 요청)에서만. llama.cpp 실행 파일은 버전 고정·sha256 검증(`llama_release.py`).
- Whisper(faster-whisper)는 제거했다 — 같은 정확도에서 맥은 CPU 전용이라 느렸고(문장당 1.14초 vs MLX 0.1초), 새 엔진이
  모든 플랫폼을 덮는다(llama.cpp CPU). 저사양 서버(RAM 4GB 이하)에서 약 2GB를 쓰는 llama.cpp가 부담일 수 있다.
- 폴더 밖 공유 캐시(`hub/blobs/`)까지 지우고(`hfcache.py`), 용량은 링크 중복 없이 센다.
- `/voice/input`은 `X-Voice-Id`로 재전송 중복을 건너뛴다(최근 120초). 단계별 시간은 응답 `timing`과 서버 로그.

## STT 엔진 벤치마크 (2026-10-02)

`scripts/stt_bench.py`로 같은 한국어 샘플에서 모델별 CER·속도·메모리를 잰다. **앱이 쓰는 모델은 어댑터 없이
`model:<카탈로그 id>`로 잰다**(실사용 엔진 코드를 그대로 호출 — 측정과 실사용이 갈라지지 않는다). 후보 엔진 비교용
어댑터 규약은 `scripts/stt_bench_engines/README.md`. 후보 라이브러리는 프로젝트 venv가 아니라 **별도 임시 venv**에
설치해서 잰다. 이 도구는 모델을 몰래 받지 않는다.

```bash
.venv/bin/python scripts/stt_bench.py make-samples /tmp/stt-bench/samples   # `say` 합성 20문장
.venv/bin/python scripts/stt_bench.py report model:qwen3-asr-0.6b-mlx model:qwen3-asr-0.6b-gguf \
    --samples /tmp/stt-bench/samples --python <엔진용 venv python>            # 엔진마다 별도 프로세스
```

실사용 문장을 녹음해 같은 폴더에 `이름.wav`(16kHz mono) + `이름.txt`(정답)로 넣으면 그쪽이 훨씬 믿을 만하다.

결과(Apple Silicon, 합성 음성 20문장, 모델 로드 후 평균):

| 엔진 | CER % | 문장당 초 | 최대 RSS MB | 비고 |
|---|---|---|---|---|
| Qwen3-ASR 1.7B 8bit (MLX) | 3.0 | 0.19 | 2550 | 모델 2.3GB |
| Qwen3-ASR 0.6B 8bit (MLX) | 6.1 | 0.10 | 1160 | 모델 971MB |
| Apple SpeechAnalyzer ko-KR | 6.4 | 0.06 | 36 | Swift 헬퍼 필요, 시스템 서비스 메모리 미포함 |
| SenseVoice-Small int8 (sherpa-onnx) | 6.4 | 0.03 | 733 | 띄어쓰기가 어긋남 |
| faster-whisper small | 6.6 | 1.14 | 1211 | (제거됨) CPU 전용이라 느림 |
| faster-whisper base | 6.7 | 0.35 | 648 | (제거됨) 이전 기본값 |
| faster-whisper small 한국어(ghost613) | 6.8 | 1.14 | 1482 | (제거됨) small과 차이 없음 |
| Qwen3-ASR 0.6B GGUF Q8 (llama.cpp, **CPU만**) | 6.1 | 0.25 | 1940~2340 | 크로스플랫폼 후보. GPU 없이 측정, 메모리는 컨텍스트 크기에 따라 다름 |
| mlx-whisper small (fp16) | 7.2 | 0.12 | 889 | (채택 안 함) faster-whisper small과 같은 크기, 약 10배 빠름 |
| sherpa-onnx Zipformer ko | 24.5 | 0.03 | 331 | 이 문장 도메인에서 약함 |
| Moonshine ko Tiny | 54.7 | 0.06 | 346 | 출력이 중간에 끊김 |

읽을 때 주의:
- **합성 음성 한 목소리(Yuna, 속도 4종)·20문장**이다. 위 상위권(6.1~7.2%)의 차이는 이 표본에서 의미 있는 차이가 아니다.
- 정답을 한글 표기로 적었기 때문에 `API`·`import`처럼 영문으로 적은 인식 결과는 틀린 것으로 센다. LLM이 읽을 용도에서는 감점이 과하다.
- `say`에서 소리가 나는 한국어 음성은 Yuna뿐이다(다른 음성은 빈 파일). 샘플 검증(`_check_audible`)이 무음을 즉시 거부한다.
- 같은 크기 모델이라도 MLX(Metal)가 faster-whisper(CPU, 맥에서 Metal 미지원)보다 약 10배 빨랐다.
- llama.cpp의 오디오 입력은 시작 로그에 **「experimental stage, may have reduced quality」**로 표시된다. 출력은 `language Korean<asr_text>본문` 형태라 본문만 잘라 써야 한다.
- 측정은 Apple Silicon CPU다. x86 PC의 CPU 속도는 확인하지 못했다.
