# Voice, clipboard, push

## Features

| Feature | Description |
|------|------|
| Voice Daemon | macOS hotkey (Ctrl+Shift+M) → STT → direct input into tmux |
| Session TTS | 세션별 끔·완료음·응답 읽기, 선택한 화면 또는 서버에서 순차 재생 |
| Voice-only mode | 🎧 button → hides the terminal and shows only a large mic (for earbud operation) |
| Web Push (P5) | ⋯ menu → "Push Notifications". Existing notifications (`/ws-notify` → Notification API) only work **while a PWA tab is alive**, so turning off the phone screen meant missing "waiting for approval." This fills that gap. No push is sent while at least one WS client is connected (to avoid the same notification arriving twice). **Requirements**: ① https — Service Workers don't even register over plain http ② iOS requires adding to the home screen as a PWA (16.4+; a subscription can't be created from a Safari tab — no workaround). **A subscription is bound to its origin** — if the trycloudflare URL changes, existing subscriptions all die, so each subscription stores its origin, mismatches are excluded from sending, and 404/410 responses are cleaned up on the spot. Notification bodies never contain commands, paths, or code (they'd show on the lock screen). The VAPID key is auto-generated at `~/.vt/vapid.json` (0600) — **deleting it invalidates every existing subscription**. SW registration is handled by `js/swreg.js` (it used to live inside `voice.js`, so the SW never registered at all when voice wasn't installed) |

### Voice Daemon (standalone macOS voice input)

전역 단축키로 녹음하고 녹음 시작에 고정한 tmux pane으로 입력한다. 기본은 서버 STT 공유다.
서버 없이 독립 사용하려면 VT_VOICE_STANDALONE=on을 명시한다. 실패 시 자동 독립 입력하지 않는다.

```bash
# Run
"$VT_PYTHON" server/voice_daemon.py &

# Usage: Ctrl+Shift+M (toggle) → speak → STT → typed into the active tmux pane
# Requires allowing the terminal app under macOS System Settings → Privacy → Accessibility
```

### 세션 음성 정책

설정 「음성 및 알림」에서 현재 세션의 끔·완료음·응답 읽기를 고른다.
우측 dock의 마이크 버튼(길게 누르기·우클릭, 준비 필요·진행 중·실패일 때만 모서리에 상태 점이 붙는다)과 커맨드 팔레트의 「음성 입력 상태」에서 설정을 열지 않고
서버 모델·필수 항목의 설치 여부와 메모리 준비 상태를 확인한다. 상태 조회는 다운로드나 모델 적재를 하지 않는다.
마이크를 누르면 같은 상태 패널에 준비·녹음·인식 결과를 표시한다. 녹음 중에는 시작 시 고정한 입력 대상과
경과 시간을 보여 주며 「녹음 종료」로 전송한다. 인식 결과는 자동으로 지우지 않는다.
모바일 dock이 숨겨진 화면에서도 이 설정의 「녹음 시작」으로 같은 입력 액션을 사용한다.
기본 off이며 「새 세션 기본값」은 처음 음성 정책을 확인하는 세션에만 복사한다. 기존 세션은 바뀌지 않는다. 출력은 이 기기 또는 서버 컴퓨터를 선택한다. 브라우저 소유권은 한 화면만 갖고
다른 화면에서는 「이 기기로 옮기기」로 옮긴다. 연결 종료 시 서버에서 대신 읽지 않는다.
음성 큐는 기기별 순차 재생(최대 10건, 2분)이며 자동 재생 차단은 「터치하여 재생」으로 표시한다.
새 녹음은 해당 출력의 재생을 중단하고 큐를 잠시 멈춘다. 다른 앱의 say/afplay는 종료하지 않는다.

Claude·Codex Stop은 서버 voice_sessions의 공통 정책을 거친다. voice_completion의 도구별 어댑터는
Claude 기록과 Codex response_item/event_msg 또는 last_assistant_message를 읽는다.
응답 끝부분 최대 200자를 읽으며 별도 LLM 요약은 하지 않는다. 식별이 모호하면 낭독하지 않는다.
해당 pane에서 `fsh voice-bind <claude|codex> <대화 ID>`로 명시적으로 연결할 수 있다.
최종 응답 어댑터가 없는 도구는 TTS 미지원이다. 출력 idle 감시는 음성을 생성하지 않는다.

STT는 녹음 시작에 대상 토큰을 발급받고 종료·재전송에 같은 pane을 사용한다. 대상이 사라지면
다른 pane으로 보내지 않고 인식 텍스트 복사를 제공한다. 첫 클릭은 모델 준비, 다음 클릭은 녹음이다.
브라우저 녹음은 최대 60초, 업로드는 4MiB다. 구 로컬 마이크 기능은 제거했으며 구 요청은 410이다.
자동 VAD·연속 녹음은 지원하지 않는다. 이어폰 미디어키는 매 접속마다 기본 off다.

TTS auto는 edge-tts → macOS say이며 브라우저가 재생할 수 있는 WAV로 로컬 음성을 만들고 실제 오디오 MIME을 전달한다. edge는 텍스트를 외부로 보낸다.
local을 선택하면 외부 요청 없이 say만 사용한다. edge 선택은 실패 시 다른 엔진으로 바꾸지 않는다.
완료 훅의 서버 장애 시 직접 OS 낭독 폴백은 없다. 기존 전역 낭독 설정은 새 세션을 자동으로 켜지 않는다.
프로토콜은 [API.md](../../API.md)를 본다.

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
