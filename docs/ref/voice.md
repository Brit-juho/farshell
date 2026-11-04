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
다른 pane으로 보내지 않고 인식 텍스트 복사를 제공한다. 입력 전 확인 단계는 위 「확인 후 전송」을 본다. 첫 클릭은 모델 준비, 다음 클릭은 녹음이다.
브라우저 녹음은 최대 60초, 업로드는 4MiB다. 구 로컬 마이크 기능은 제거했으며 구 요청은 410이다.
연속 녹음(발화마다 저절로 다시 시작)은 지원하지 않는다. 청크 경로에서만 서버 VAD 자동 종료(「자동 종료」, 기본 off)와
녹음 중 부분 결과(「부분 결과 표시」, 기본 on)가 있다 — 아래 「녹음 중 처리」. 이어폰 미디어키는 매 접속마다 기본 off다.

TTS auto는 edge-tts → macOS say이며 브라우저가 재생할 수 있는 WAV로 로컬 음성을 만들고 실제 오디오 MIME을 전달한다. edge는 텍스트를 외부로 보낸다.
local을 선택하면 외부 요청 없이 say만 사용한다. edge 선택은 실패 시 다른 엔진으로 바꾸지 않는다.
완료 훅의 서버 장애 시 직접 OS 낭독 폴백은 없다. 기존 전역 낭독 설정은 새 세션을 자동으로 켜지 않는다.
프로토콜은 [API.md](../../API.md)를 본다.

### 입력 대상 고정 (브라우저, 기기별)

기본은 「현재 보는 세션 따라가기」 — 녹음 시작 때 `window.activeId`가 대상이다. 음성 상태 패널(`#voice-pin-select`)이나
설정 「음성 및 알림」의 「입력 대상」에서 특정 세션을 **고정**하면 포커스와 무관하게 그 세션으로 간다.

- 값은 이 브라우저의 localStorage `vt_voice_pin`(tmux 세션 **이름**)이다. 저장이 막히면 `window.__vtVoicePinMemory`로 이 화면에서만 동작한다.
  웹 PTY id가 아니라 이름을 쓰는 이유: PTY id는 화면을 새로 열 때마다 달라져 새로고침 뒤 고정이 풀린다.
- 녹음 시점에 그 이름을 가진 열린 세션 id를 `window.sessions`에서 찾아 `/voice/target`에 보낸다(같은 이름이 여럿이면 활성 화면 우선).
  원격 호스트 세션과 tmux가 아닌 세션은 후보가 아니다.
- 고정한 세션이 열려 있지 않으면 **활성 세션으로 대신하지 않고** 마이크·모델 준비·서버 요청 전에 녹음을 막는다
  (「고정한 입력 대상이 없습니다」, 마이크 버튼 상태 점 error). 서버도 대상이 사라진 토큰을 다른 pane에 보내지 않는다.
- 구현은 `frontend/js/voice/target-pin.js`다. voice.js 번들(`recording.js`)과 app.js 번들(`status-panel.js`·설정)이 각자 사본으로
  가져가므로 모듈 상태를 두지 않고 `core/store.js`를 import하지 않는다(`recording.js` 상단 경고).
- 응답 읽기(TTS) 정책은 세션별 그대로다. Voice Daemon은 이 설정을 보지 않는다.

### 확인 후 전송 (`voice.confirm`)

텍스트 + Enter를 바로 입력하던 계약은 다음으로 바뀌었다. 전역 설정 `voice.confirm`(`off | shell | on`, 기본 `shell`;
`workspace.json` settings, 서버가 알 수 없는 값은 `shell`로 본다) 정책은 `server/voice_confirm.py`가 판정한다.

| 값 | 동작 |
|---|---|
| `off` | 확인하지 않는다. 위험 패턴도 확인하지 않는다 |
| `shell` | AI 에이전트 pane이면 바로 입력. 일반 셸이거나 **판별이 불확실하면**(vim·ssh·python, tmux 조회 실패) 확인 |
| `on` | 항상 확인 |

- pane 판별은 `agent_detector`의 판정(`KNOWN_AGENTS`, 셸 아래 자식 프로세스 스캔 포함)을 대상 pane 하나에 적용한다(`voice_confirm.pane_kind`).
  `agent`만 바로 입력하고 `shell`·`unknown`은 확인한다.
- **위험 패턴**은 `off`가 아니면 pane 종류와 무관하게 항상 확인한다. 목록은 `voice_confirm.DANGEROUS_PATTERNS` 한 곳에 있고
  `server/tests/test_voice_confirm.py`가 잠근다(rm -r/-f, sudo, curl/wget | sh, git push -f/--force, git reset --hard, git clean -f,
  mkfs, dd of=, `> /dev/…`(null·stdout·stderr 제외), chmod/chown -R, 포크 폭탄). NFKC 정규화 후 영문 표기로만 검사한다.
- 확인이 필요하면 `/voice/input`은 **인식만 하고 입력하지 않는다.** 응답에 `pending_confirm: true`, `text`, `confirm_token`,
  `confirm_reason`(`shell|unknown|always|dangerous`), `confirm_ttl_sec`, `target_name`이 든다. 웹은 패널의 편집 상자(`#voice-confirm`)에
  보이고 「입력」이 `POST /voice/inject {token, text, voice_id}`를 부른다. 「취소」는 아무것도 보내지 않는다.
- 확인 화면을 그릴 수 있는 클라이언트만 확인 단계를 거친다 — 웹이 `X-Voice-Confirm: 1`을 보낸다. 이 헤더가 없는 호출자(Voice Daemon,
  옛 클라이언트)는 확인할 방법이 없으므로 기존대로 바로 입력하되, 위험 패턴만 입력하지 않고 `error`로 알린다(`off`가 아닐 때).
- `/voice/inject`의 검증: 토큰 소유 주체(`voice_targets.principal`) 일치, 대상 pane `valid()` 재검증(사라졌으면 `target_gone` 409,
  토큰 폐기, 다른 pane으로 보내지 않음), 제어문자 제거(`paste_prepare.strip_unsafe_control_chars`, 입력 경로와 같다), 길이 상한
  4000자(413), 빈 텍스트 400, 발급 때의 `voice_id`와 일치. 같은 토큰의 동시 요청은 하나만 통과하고(429 `busy`) 입력이 끝나면
  같은 토큰·같은 `voice_id`의 재요청은 `duplicate`로 응답한다(입력 없음). 없는·만료·남의 토큰은 모두 같은 409 `expired`다.
  연결이 끊겨 취소돼도 이미 입력됐을 수 있어 토큰을 되돌리지 않는다. 입력 도구 호출 실패(`send_failed` 502)만 같은 토큰으로 다시 시도할 수 있다.
- 확인 토큰 TTL은 300초(사용자가 고칠 시간), 1회용, 서버 메모리에만 있다(재시작하면 사라진다). 대기 한도 256개.
- 새 녹음을 시작하면 이전 확인 대기는 클라이언트가 버린다.

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

## 청크 업로드 (PCM)

긴 녹음 중 터널이 끊기면 통째로 잃던 문제를 줄이려고, AudioWorklet이 되는 브라우저는 녹음 중에 조각을 올린다.
엔드포인트 스키마와 오류 코드는 [API.md](../../API.md)의 `/voice/chunk`. 여기는 구조와 이유만 적는다.

- **클라이언트**(`frontend/js/voice/`, 모두 voice 번들): `pcm-capture.js`(AudioContext + 워클릿, 입력 샘플레이트가
  16kHz가 아니면 `pcm.js`의 면적 평균 리샘플) → `pcm.js`(PCM16 LE, 16000바이트=500ms 청크) → `chunk-upload.js`
  (한 번에 한 요청, 누적 `ackSeq`로 정리, 실패는 같은 seq 재전송) → `pcm-session.js`가 묶는다. `recording.js`는
  분기만 한다. 워클릿 소스는 문자열 상수(`pcm-worklet-src.js`)를 Blob URL로 올린다 — 별도 asset을 두면 고정
  산출물 목록(`voice.js`)과 `sw.js` precache가 늘고, `?raw` import는 테스트용 `vm-esm` 로더가 못 읽는다. 서버 CSP는
  `frame-ancestors`뿐이라 blob 모듈이 막히지 않는다. 워클릿 미지원·보안 컨텍스트 아님·`addModule` 실패 시
  MediaRecorder + `/voice/input` 경로로 같은 스트림을 넘겨 폴백한다(콘솔 `[voice] 캡처 경로:` debug 로그에 어느 쪽인지 남는다).
  iOS Safari는 AudioContext를 제스처 안에서 만들어야 해서 `startRecording` 맨 앞(await 전)에서 만든다.
- **재전송 한계**: 일시 실패(네트워크·429/502/503/504)는 backoff 3회 뒤에도 녹음을 쌓으며 25초까지 같은 seq를 다시 보낸다
  (서버가 30초 무응답이면 폐기하므로 그 안). 서버가 상태를 잃었으면(응답 ackSeq < 보낸 seq) 재시도하지 않고 실패한다.
  실패하면 「다시 보내기」가 남은 조각으로 재시도한다(15초 TTL 안).
- **서버**(`server/voice_chunks.py`): 녹음마다 `(principal, voice_id)` 상태 — 이어 붙은 조각 `parts[seq]`, 앞 seq가 빈 채
  기다리는 `pending`, 누적 바이트, 마지막 활동 시각. 모두 await 없이 한 번에 바뀌어 락이 없다. 상한: 전체 60초(1,920,000B,
  엄격), 조각 5초, 보류 1000개/30초, seq < 4000, 동시 녹음 64건. 같은 principal의 새 녹음은 이전 열린 녹음을 폐기한다
  (STT가 직렬이라 1건). 30초 무응답 폐기와 만료 묘비는 요청마다와 10초 주기 작업(`sweep_loop`)이 치운다.
  완결되면 16kHz mono WAV로 감싸 `/voice/input`과 **같은 중복 방지·인식·주입 경로**(`_voice_dedup_run`)로 넘긴다 —
  `_voice_done` 캐시, 지문 충돌 409, 동시 final이 같은 인식에 합류하는 동작이 그대로다. 인식이 예외(429 등)면 조각을 남겨
  같은 final 재전송으로 다시 시도할 수 있다. 끝난 녹음은 조각 메모리를 놓고 결과만 120초 남긴다.
- **스냅샷**: `Recording.contiguous_pcm()`은 이어 붙은 앞부분 전체, `tail_pcm(n)`은 끝 n바이트 이하(조각 경계)의 복사본이다.
  부분 인식은 `tail_pcm`만 쓴다(최대 20초 = 640KB).
- **음량 미터**: `meter.js`(순수 함수)가 RMS와 적응식 노이즈 플로어로 0~1 레벨과 클리핑 표시를 계산한다(상승 40ms, 하강 450ms
  시정수; 플로어는 빠르게 내려가고 15초에 걸쳐서만 올라가며 -26dBFS를 넘지 않는다). voice 번들이 `vt:voice-level` 이벤트를
  보내고 app 번들의 `status-panel.js`가 `#voice-meter` 막대만 그린다(두 번들은 이벤트로만 만난다). `prefers-reduced-motion`이면
  5단계·초당 4회로 줄인다. MediaRecorder 폴백 경로에는 미터가 없다. 경과 시간은 기존 「N초 / 60초」 줄이다.

## 녹음 중 처리 (서버 VAD 자동 종료·부분 결과)

청크 경로(`/voice/chunk`)에만 있다. MediaRecorder 폴백·Voice Daemon에는 없다. 응답 필드는 [API.md](../../API.md).

- **구조**: `server/voice_vad.py`(순수 판정 클래스) ← `server/voice_live.py`(녹음 한 건마다 `Live`, `Recording.live`에 붙음) ←
  `routes/voice.py`. 새 녹음의 첫 조각에서 설정 `voice.autoStop`·`voice.partialResults`(workspace.json `settings`, 전역)를
  한 번 읽어 그 녹음 동안 고정한다. 둘 다 끄면 VAD도 돌지 않는다. `Live`의 예외는 청크 수신을 막지 않는다(로그만).
  수집기가 기록을 완결(final)·취소·만료·교체·완료하거나 서버가 종료(`voice_chunks.reset()`)하면 `Live.close()`가 진행 중
  부분 인식 작업을 취소하고 결과를 버린다.
- **VAD**(`voice_vad.EnergyVad`): 20ms 프레임 RMS를 적응형 노이즈 플로어와 비교한다. 처음 200ms는 보정(최소 RMS를 플로어로,
  발화로 안 셈), 플로어는 조용해지면 300ms 시정수로 내려가고 비발화 프레임에서만 15초 시정수로 올라간다(상·하한은 `meter.js`와
  같은 -66~-26dBFS). 프레임이 `플로어+9dB` 이상이고 -45dBFS 이상이면 발화. 발화가 300ms 쌓이면(사이 끊김 200ms 초과 시 다시
  셈) 확정, 확정 뒤 1초 조용하면 끝점. 시간은 받은 오디오 길이로 센다(네트워크 지연과 무관). 상수 근거는 파일 주석.
  알려진 한계: 잡음이 9dB 넘게 한꺼번에 커지면 그 잡음을 발화로 봐 끝점이 나지 않는다(사용자가 직접 종료 — 잘못 끊는 것보다
  안전한 쪽). 문장 사이 1초 넘는 침묵은 끊긴다(그래서 기본 off).
- **자동 종료**: 켜져 있고 끝점이면 ack 응답에 `endpoint: true`. 클라이언트(`pcm-session.js`의 `makeAckHandler`)가 녹음당 한 번
  `stopRecording()`과 같은 길로 final을 보낸다. 서버는 녹음을 스스로 끝내지 않는다(남은 조각은 클라이언트에 있다). 발화가 확정된
  적이 없으면 끝점도 없고 60초 상한은 그대로다.
- **부분 결과**: 발화 확정 뒤, 새 오디오가 1.5초 이상 늘고 그 사이 새 발화가 있으면 최근 20초(`tail_pcm`)를 WAV로 감싸
  `stt_service.transcribe_partial`에 넘긴다. 이 함수는 **기다리지 않는다** — 엔진 락을 못 잡거나 줄 선 요청(`_waiting`)이 있거나
  고른 모델이 메모리에 없으면 None(건너뛰고 다음 조각에서 다시 시도). 모델을 올리지 않고, 줄 수·`last_error`·`timing`을 바꾸지
  않는다. 녹음당 진행 중 부분 인식은 1건. 결과는 `partial {text, seq, clipped}`로 이후 ack마다 실린다. 클라이언트는 더 큰 seq만
  `vt:voice-partial` 이벤트로 보내고, 상태 패널이 `#voice-partial`에 보조 텍스트 색으로 표시한다(녹음·인식 중에만, 최종
  결과·오류·새 녹음에 지움, 입력하지 않음, aria-live 없음). 로그는 `voice.partial audio_ms=… stt_ms=… chars=…`만 남기고
  텍스트는 남기지 않는다. 실패해도 녹음은 계속한다.
- **STT 부하**: 부분 인식은 말하는 동안 대략 1.5초마다 최대 20초 오디오를 한 번 더 인식한다. 다음 간격은 `max(1.5초,
  직전 부분 인식 시간 × 3)`이라 엔진 점유율은 1/3 이하로 묶인다. 이미 엔진에 들어간 부분 인식은 멈출 수 없어서, 그 사이 도착한
  최종 인식(같은 녹음이든 다른 사용자든)은 **최대 부분 인식 한 번만큼** 기다린다. 아래 벤치마크의 문장당 시간(MLX 0.6B 0.10초,
  CPU llama.cpp 0.25초)은 짧은 문장 기준이고 20초 창은 더 걸린다 — 20초 창의 실제 시간은 아직 재지 않았다. 저사양 서버면 「부분 결과 표시」를 끈다. 기본 on으로 둔 이유: 점유율 상한과 non-blocking 건너뛰기로 최종
  결과의 정확성은 바뀌지 않고 지연 상한이 정해져 있으며, 단일 사용자 맥(권장 MLX)에서 체감 비용이 작다.
- **바지인**: 서버 VAD 발화 시작으로 TTS를 멈추는 것은 **구현하지 않았다**. 녹음 시작에 이미 `pauseVoice(true)`가 이 화면의
  재생을 멈추고 서버 큐를 멈추며(`voice_barge`), 녹음 동안 유지된다 — 발화 시작에 같은 화면을 다시 멈춰도 얻는 게 없다. 서버
  컴퓨터 출력(`destination: server`)이나 다른 기기 재생을 멈추는 것은 "녹음하는 화면의 출력만 멈춘다"는 정책을 바꾸는 일이라
  하지 않았다. 대신 남은 틈을 클라이언트(`tts.js`)에서 막는다: 녹음 중 `/ws-voice`가 재연결되면(서버 `disconnect`가 그 화면의
  일시정지를 지운다) 일시정지를 다시 보내고, 그 틈에 온 음성은 재생하지 않고 `cancelled`로 알린다(120초 안전 해제).
- **테스트**: `server/tests/test_voice_vad.py`(합성 사인파+잡음), `test_voice_live.py`(라우트 응답·작업 정리·락 경합, 가짜 STT),
  `frontend/tests/voice-ack-handler.test.js`·`voice-pcm-flow.test.js`·`voice-status-panel.test.js`·`voice-session-flow.test.js`.

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
