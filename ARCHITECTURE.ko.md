# FarShell 아키텍처

> **버전:** v2.0.0 — 2026년 9월에 프런트엔드가 대대적으로 재구조화됐다
> (로컬 전용 [`docs/plan-2.0/`](./docs/plan-2.0/) 참고 — gitignore 대상이라
> GitHub엔 안 올라감). 릴리스 이력은 [CHANGELOG.md](./CHANGELOG.md),
> REST/WebSocket 전체 레퍼런스는 [API.md](./API.md) 참고 — 이 문서가 엔드포인트
> 표를 따로 들고 있지 않는 이유는 예전에 그렇게 했다가 드리프트가 났기 때문이다.

이 문서는 기여자와 LLM이 레포 구조를 빠르게 이해하기 위한 지도다. 모노레포
전환 대신 **논리적 경계**만 명시한다.

---

## 1. 3-Plane 모델

```
┌──────────────────────────────────────────────────────────────┐
│ Control Plane — 시작/정지/진단 (사용자 한정 동작)              │
│   bin/fsh, install.sh, ~/.vt.env, server/tui/ ("fsh manage")  │
└──────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────┐
│ Work Plane — 실제 작업이 벌어지는 곳                           │
│   tmux 세션 (dev) ← Claude / aider / codex / 쉘 / psql ...    │
│   ↑ Voice Daemon이 send-keys로 키 주입                        │
│   ↑ 모바일/데스크톱 브라우저가 WebSocket으로 attach(E2E 가능)  │
└──────────────────────────────────────────────────────────────┘
                            ▲                       ▲
                            │                       │
┌───────────────────────────┴─────┐   ┌─────────────┴──────────┐
│ Voice Plane — STT/TTS          │   │ Network Plane           │
│   server/voice_handler.py      │   │   cloudflared 터널      │
│   server/voice/ (데몬 패키지)   │   │   Tailscale (D9)       │
│   server/local_mic.py          │   │   토큰/비밀번호 인증    │
│   frontend/js/voice/ (별도 번들)│   │   ntfy/Telegram 푸시   │
└────────────────────────────────┘   └─────────────────────────┘
```

**핵심 아이디어**: tmux 세션이 **단일 진실의 원천(single source of truth)**.
데스크톱 iTerm, 모바일 PWA, Voice Daemon이 모두 같은 tmux에 붙어서 동작한다.

### 1.1 단일 tmux 서버 원칙 (Phase 6)

fsh의 모든 클라이언트는 격리된 tmux 소켓 `-L fsh`(`VT_TMUX_SOCKET` 환경변수로
오버라이드 가능)에 접속한다. 사용자의 기존 `tmux ls` 세션과 분리된다.

| 클라이언트 | 호출 형태 | 출처 |
|------------|-----------|------|
| `bin/fsh` (CLI) | `${TMUX_BASE[@]} ...` (`tmux -L fsh`) | `bin/fsh` 상단 정의 |
| `server/main.py` (PTY) | `tmux -L fsh attach-session ...` | `pty_manager.py` |
| `server/voice/daemon.py` | `TMUX_BASE = ["tmux", "-L", TMUX_SOCKET]` | Phase 6 #6-1 |
| `server/tui/`(`fsh manage`) | `tmux_runner.py`/`helpers.py` 경유 tmux 호출 | W4-1 |
| Stop hook (`tts_hook.sh`) | (TTS만, tmux 직접 호출 없음) | — |

소켓을 통일하지 않으면 Voice Daemon 입력이 모바일·웹과 분리되어 "왜 안
들어가지?" 류 디버깅이 발생한다.

---

## 2. 디렉토리별 책임

### `bin/` — CLI 진입점 (Control Plane)
| 파일 | 책임 |
|---|---|
| `fsh` | macOS/Linux. CLI — 서브커맨드 라우팅, 프로세스 수명 관리(서버·터널·음성 데몬), iTerm 자동 오픈, 진단 |
| `fsh.ps1` | Windows PowerShell 버전(네이티브 Windows 자체는 미지원 — WSL2 사용자가 PowerShell에서 fsh를 돌릴 때용) |

전체 서브커맨드 목록은 `fsh help`(또는 [CLI.ko.md](./CLI.ko.md)) 참고 —
원래의 `voice`/`mobile`/`start`/`stop` 세트를 훨씬 넘어섰다(큐, 핫키,
기기 관리, 템플릿 관리, 터널 관리, `fsh manage` TUI 등). 프롬프트 스니펫은
웹 UI 전용이다 — `fsh snippet` 서브커맨드는 없다.

### `server/` — FastAPI 백엔드 (Work + Voice Plane)

`main.py`는 FastAPI 진입점 — 미들웨어/인증을 연결하고 `server/routes/`
아래 라우터 11개를 마운트한다(아래). REST 엔드포인트뿐 아니라 WebSocket
엔드포인트도 각 라우터가 직접 소유한다(`routes/pty.py`의 `/ws/{id}`·
`/ws-notify`, `routes/tmux.py`의 `/ws-preview/{tmux_name}`,
`routes/agents.py`의 `/ws-agent`, `routes/system.py`의 `/ws-workspace`).

**세션 / PTY / tmux**
| 파일 | 책임 |
|---|---|
| `pty_manager.py` | PTY fork, WebSocket broadcast, scrollback 버퍼(tmux 대체의 핵심 모듈) |
| `session_store.py` | 세션 메타데이터(이름, tmux_name), `secrets.token_urlsafe(12)` id 생성 |
| `tmux_runner.py` | 공용 tmux 명령 실행 헬퍼 |
| `tmux_target.py` | "어느 tmux pane인가" 판정의 단일 진실 원천 + 텍스트 주입(음성·큐·핫키가 공유) |
| `preview.py` | 읽기 전용 tmux pane 라이브 프리뷰(WS push, 진짜 tmux 클라이언트로 안 잡힘) |
| `workspace.py` | 기기별 워크스페이스 동기화(탭 순서·활성 세션·UI 설정) → `~/.config/vt/workspace.json` |

**인증 / 안전**
| 파일 | 책임 |
|---|---|
| `auth.py` | 웹 로그인(scrypt 비밀번호 해시 + HMAC 세션 쿠키), 기기 화이트리스트 + TOTP 게이트, 1회용 등록 티켓 |
| `crypto_channel.py` | E2E WebSocket 암호화(X25519 임시 키 + Ed25519 identity 서명 + NaCl SecretBox) — 클라이언트 측 짝은 `frontend/js/term/e2e.js` |
| `fsguard.py` | 코드 뷰어의 파일 열람 루트 제한·경로 검증·거부 목록의 단일 진실 원천 |
| `safe_mode.py` | 설정 가능한 위험 명령 목록을 사전 차단 |
| `network_access.py` | `localhost`/`lan`/`tailscale`/`all` 네트워크 모드 → CIDR 화이트리스트, bind host 결정 |

**음성**
| 파일 | 책임 |
|---|---|
| `voice_handler.py` | STT(mlx-whisper → faster-whisper) · TTS(Kokoro → edge-tts → say) |
| `output_watcher.py` | PTY 출력 idle 감지 → TTS + 푸시 알림 |
| `local_mic.py` | 데스크톱 로컬 마이크 REST API(음성 프로필 전용 — core 프로필에서도 정상 동작하도록 무조건 import됨) |
| `voice_daemon.py` | 얇은 shim — 실제 구현은 아래 `server/voice/` 패키지 |
| `server/voice/`(패키지) | `daemon.py`(핫키+미디어키 메인 루프) · `config.py`(`~/.vt.env` 파싱) · `media_keys.py`(macOS 이어폰 Play/Pause) · `recorder.py`(녹음→STT→tmux 주입) · `stt.py`(mlx-whisper 우선, faster-whisper 폴백) · `tmux_target.py`(최상위 모듈 위 compat shim — 타깃 판정만을 위해 `pynput`을 끌어오지 않으려는 것) |

**에이전트 상태**
| 파일 | 책임 |
|---|---|
| `agent_detector.py` | pane에서 어떤 AI CLI(Claude Code 등)가 돌고 있는지 감지 |
| `agent_status.py` | **에이전트 상태 머신**(`idle`/`working`/`waiting`/`done`, `error`는 예약). `post`는 상태를 안 바꾸고(다음 도구가 이어질 수 있다), `stop`은 엔트리를 유지한 채 `done`으로 둬 새로고침을 견딘다. TTL 만료(working 15분/waiting 2분/done 30분)는 백그라운드 태스크가 아니라 읽기·쓰기 진입점에서 지연 실행된다 — 이벤트 루프가 없는 곳에서도 같은 규칙이 성립한다 |
| `pane_resolve.py` | "이 훅 이벤트가 어느 tmux 세션인가"의 3단 해석: 자기보고 `$TMUX_PANE` → cwd(유일할 때만) → 포기. pane id는 소켓별로 매겨지므로 개인 tmux의 `%12`가 우리 것과 겹칠 수 있어 `$TMUX`의 소켓을 먼저 검증한다 |
| `agent_prompt_detect.py` | PTY 출력에서 승인 대기(`waiting`) 감지. `auto_responder`와 같은 슬라이딩 윈도우 구조지만 **아무것도 쓰지 않고** 상태만 알린다. 패턴은 `server/detect/*.toml`로 외부화 — CLI 문구가 바뀌어도 코드를 안 고친다 |
| `claude_hooks.py` | `~/.claude/settings.json` 멱등 등록기(`fsh hooks install`). 직접 넣은 훅은 보존하고, 저장소가 옮겨지면 우리 항목만 갱신하며, 쓰기 전에 백업한다 |
| `usage/` | 사용량 provider 추상화(`base`/`clauth`/`null` + 팩토리). `~/.clauth/status.json` **하나만** 읽고 **필드 화이트리스트**로 내보낸다 — 모르는 필드는 통과시키지 않고 버려서, clauth가 나중에 추가하는 필드가 공개 터널로 새지 않는다 |
| `auto_responder.py` | 신뢰 프롬프트에 대한 옵트인 자동 응답기 |

**네트워크 / 터널**
| 파일 | 책임 |
|---|---|
| `tunnel.py` | Cloudflare Tunnel 상태 감지 |
| `tunnel_watchdog.py` | cloudflared의 좀비 재연결 상태(프로세스는 살아있지만 QUIC 컨트롤 스트림만 끊김) 감지 → 터널 자동 재시작 |
| `tunnel_registry.py` | `fsh tunnel expose`로 연 추가 포트 터널 레지스트리 |
| `tailscale.py` | Tailscale 상태 감지(`tailscale status --json`) — `tunnel.py`와 같은 패턴 |
| `clipboard_daemon.py` | macOS `NSPasteboard.changeCount` 폴링 → 웹 클립보드로 변경 사항 push |

**포트 / 큐 / 스니펫**
| 파일 | 책임 |
|---|---|
| `portscan.py` | 포트 대시보드용 리스닝 포트 스캔 + 안전한 종료 로직 |
| `queue_runner.py` | 프롬프트 큐를 tmux pane에 순차 투입 — grace period/safe-mode/pane 생존 여부로 게이팅 |
| `queue_store.py` | 프롬프트 큐 저장소(`~/.vt/queue.json`) |
| `snippet_store.py` | 프롬프트 스니펫 라이브러리 저장소 |

**푸시 / 알림**
| 파일 | 책임 |
|---|---|
| `notify.py` | ntfy.sh/Telegram 비동기 푸시 브릿지 |
| `push.py` | Web Push(VAPID) 구독 관리 |

**기타 / 유틸리티**
| 파일 | 책임 |
|---|---|
| `platform_utils.py` | macOS/Linux/WSL2 크로스플랫폼 유틸리티(기본 셸, tmux 경로, 로컬 IP, TTS 폴백) |
| `vt_env.py` | `~/.vt.env` 파서 — `server/voice/config.py`와 `clipboard_daemon.py`가 공유 |
| `ttl_cache.py` | 범용 TTL 캐시 유틸리티 |
| `deps.py` | 공유 싱글턴 인스턴스(가벼운 DI 대체) |
| `tts_hook.sh` | Claude Code Stop hook — 응답 완료 시 TTS + ntfy |

**`server/tui/` — `fsh manage` TUI(Textual)**
| 파일 | 책임 |
|---|---|
| `app.py` | 메인 Textual `App` — 세션/타깃/핫키/상태 화면 |
| `helpers.py` | 각 화면이 공유하는 서버 HTTP 호출·tmux 호출·상태 파일 I/O |
| `modals.py` | 이름변경 + 확인 모달 다이얼로그 |
| `server/tui_manager.py` | 얇은 shim 진입점 — `voice_daemon.py`가 `server/voice/`에 대해 갖는 관계와 동일 |

**`server/routes/` — `main.py`가 마운트하는 REST/WS 라우트 모듈**
| 파일 | 마운트 |
|---|---|
| `pty.py` | `/api/sessions` CRUD, `/api/upload`, `/api/download` — PTY 세션 수명주기 + 파일 전송 |
| `tmux.py` | `/api/tmux/*` — 세션 CRUD + 라이브 프리뷰 + 맥에서 열기 |
| `voice.py` | `/voice/*` — STT/TTS I/O, 취소, 상태/preload |
| `agents.py` | `/api/agents*`, `/api/agent/event`, `/api/agent/status` — 에이전트 감지 + 훅 상태 |
| `files.py` | `/api/fs/*`, `/api/git/*` — 읽기 전용 파일 브라우저 + git status/diff/stage/commit (P2, D16) |
| `ports.py` | `/api/ports*` — 포트 대시보드(블로킹 호출은 `asyncio.to_thread`로 오프로드) |
| `queue.py` | `/api/queue*` — 프롬프트 큐 |
| `snippets.py` | `/api/snippets*` — 프롬프트 스니펫 라이브러리 |
| `push.py` | `/api/push/*` — Web Push 구독 |
| `clipboard.py` | `/api/clipboard/push` — 클립보드 데몬 → `/ws-notify` 브로드캐스트 |
| `system.py` | `/api/capabilities`, `/api/tunnel/status`, `/api/tailscale/status`, `/api/safe-mode`, `/api/notify/client-event` — 상태/진단 |

**`server/hooks/`**
| 파일 | 책임 |
|---|---|
| `tmux_client_notify.sh` | tmux client-attached/detached 훅(D9) → `/api/notify/client-event` POST |

### `frontend/` — xterm.js PWA, Vite 빌드

2026년 9월 재구조화 이후 **모든 프런트엔드 스크립트가 진짜 ES 모듈**
(`import`/`export`)이고, Vite가 library 모드로 번들한다. 산출물은 2개,
둘 다 파일명이 고정(해시 없음)이라 `sw.js`의 오프라인 프리캐시가 계속 유효하다:

- `frontend/dist/app.js` + `frontend/dist/app.css` — 메인 진입점
  (`frontend/js/main.js`)이 아래 거의 전부를 정적 import한다.
- `frontend/dist/voice.js` — `frontend/js/voice/`가 **완전히 독립된** 별도
  Vite entry로 빌드된다. `frontend/js/agent/status.js`가 `/api/capabilities`로
  음성 설치 확인 후에만 `<script type="module">` 태그로 지연 로드한다 —
  터미널만 쓰는 사용자는 이 바이트를 전혀 안 받는다. 굳이 완전히 독립적이어야
  하는 이유는, 두 entry가 *공유*하는 동적 import 청크가 있으면 Rollup이 그
  청크에 해시 붙은 파일명을 붙여 "파일명 고정" 계약이 깨지기 때문이다 —
  자세한 경위(이 문제가 실제로 낸 버그: voice 번들에서 `core/store.js`를
  import하면 그 모듈 상태가 조용히 복제돼 voice.js 로드 후
  `window.sessions`가 깨졌던 사고와 수정)는 `vite.config.js` 주석 참고.

| 디렉토리 | 책임 |
|---|---|
| `js/core/` | `env.js`(API_BASE/토큰/쿠키 교환), `api.js`(`apiFetch`/`vtFetch`/`vtEsc`), `store.js`(세션 상태 — `getSession`/`activeSession`/`setActive`/`subscribe`), `dom.js`(인라인 `onclick` 29개를 대체한 `data-action` 클릭 위임 레지스트리) |
| `js/term/` | 구 2000줄 `terminal.js`의 후신 전부: `e2e.js`(E2E 핸드셰이크 클라이언트 측), `session.js`(`addSession`/`switchTo`/탭 수명주기 — 가장 위험했던 분할, 억지 3분할 대신 `tab-dom.js`/`xterm-setup.js`/`ws.js` "조립 부품"을 부르는 오케스트레이터 하나로 유지), `clipboard.js`, `touch.js`, `links.js`, `selection.js`, `resize.js`, `workspace.js`(탭 순서 영속화), `conn-overlay.js`, `keybar.js`, `tmux-panel.js`, `guide.js`, `boot.js`(`bootApp()`) |
| `js/agent/` | 구 `grid.js`의 후신: `badges.js`(어떤 아이콘), `status.js`(`/ws-agent`, 작업중 감지, capability 게이팅), `preview.js`(라이브 프리뷰 그리드 뷰 자체) |
| `js/panels/` | `panel.js`(코드 뷰어/포트/큐/스니펫 공용 모달 패널 셸) + `viewer/`(구 `viewer.js`의 후신: `state.js`, `shell.js`, `tree.js`, `file.js`, `diff.js`, `git.js` — `shell.js`↔`tree.js`, `shell.js`↔`git.js`는 의도적인 순환 import. 아래 `picker.js`↔`term/session.js`와 같은 이유) |
| `js/voice/` | 구 최상위 `voice.js`의 후신: `recording.js`, `tts.js`, `notify.js`, `media-session.js`, `index.js`(진입점). 위에서 설명한 대로 별도 번들로 빌드됨 |
| `js/ui/` | `toast.js`(통합 토스트 — voice.js가 별도 번들이라 여전히 window 브리지 필요), `favicon.js`(동적 탭 배지 canvas, UMD 방식), `moreMenu.js` |
| `js/push/` | `swreg.js` — Web Push용 Service Worker 등록 |
| `js/lib/` | `ansilex.js`, `difflex.js`, `keyseq.js` — 순수 로직, UMD로 감싸 브라우저(`window.VTAnsiLex`)와 Node 테스트(`require(...)`) 양쪽에서 재사용 |
| `js/layout/` | 2.0 셸. `store.js`/`tree.js`(분할 pane 트리 — 순수 함수 + 그 상태를 들고 있는 유일한 곳), `panes.js`(재귀 렌더러 — 기존 세션 wrapper를 재생성하지 않고 옮긴다), `dnd.js`(5구역 드롭존), `resizer.js`, `compact.js`(<720px + 터치 렌더 모드), `pane-picker.js`, `rail.js`(좌측 rail), `right-rail.js`(우측 사용량 레일, ≥1024px), `clients.js`(연결된 화면), `persist.js`(레이아웃 영속화 — leaf에 `{id, tmux}`를 적어 새 PTY id로 바뀌어도 tmux 세션을 다시 찾는다), `breakpoints.js` |
| `js/theme.js`, `search.js`, `picker.js`, `ports.js`, `queue.js`, `snippets.js`, `quickopen.js`, `pushui.js`, `gate.js`, `main.js` | 최상위 기능 모듈들 + 앱 진입점. `gate.js`는 의도적으로 classic(비모듈) 스크립트로 남은 유일한 파일 — 어떤 ES 모듈(defer)보다 먼저 로그인 게이트를 실행해야 하기 때문 |
| `sw.js` | Service Worker — 오프라인 캐싱, 프리캐시 목록 |
| `manifest.json` | PWA 매니페스트 |

### 루트
| 파일 | 책임 |
|---|---|
| `vite.config.js` | 프런트엔드 빌드 설정 — library 모드, 완전히 독립된 두 entry(위 참고), 해시 없음, minify 끔 |
| `styles/` | `main.css`(Tailwind 진입점) + `layers/legacy.css`(Tailwind 도입 전 손으로 쓴 CSS, 캐스케이드에서 항상 이기도록 레이어 밖에 둠) |
| `install.sh` | Python venv 생성, 프로필별 패키지 설치, fsh 심링크, ~/.vt.env 초기화, `npm ci && npm run build` 실행 |
| `requirements-core.txt` | 터미널 전용(~50MB) |
| `requirements-voice.txt` | 음성 추가 의존성(~1.5GB) |
| `.github/workflows/ci.yml` | CI — PR/push마다 `npm test`/`pytest`, 프런트엔드 빌드 산출물 형태 검증(해시 없는 파일명, 크기 상한) |

### `.claude/skills/` — Claude Code 스킬
| 파일 | 트리거 |
|---|---|
| `fsh/SKILL.md` | 전역: "음성 모드", "모바일 접속" 등 |
| `fsh-voice.md` | Voice Daemon 수동 설치/실행 |
| `fsh-mobile.md` | 모바일 adb 테스트 |
| `fsh-start.md` | 서버 수동 시작 |

---

## 3. 주요 데이터 흐름

### 3.1 데스크톱 음성 입력 (Voice Daemon)
```
Ctrl+Shift+V (pynput, server/voice/daemon.py)
  → sounddevice 16kHz mono 녹음 (recorder.py)
  → mlx-whisper / faster-whisper STT (stt.py)
  → tmux_target.py가 pane 판정 → tmux send-keys "<text>"
```

### 3.2 모바일/데스크톱 음성 입력 (PWA)
```
🎤 버튼 (frontend/js/voice/recording.js, 별도 번들)
  → MediaRecorder (webm/opus)
  → apiFetch POST /voice/input?session_id=...
  → server/routes/voice.py → voice_handler.transcribe (ffmpeg 변환 포함)
  → pty_manager.write(session_id, text) → PTY → tmux
```

### 3.3 Claude 응답 완료 → TTS + 푸시
```
Claude Code Stop hook → server/tts_hook.sh
  ├─ transcript에서 마지막 assistant 응답 추출
  ├─ POST /voice/output → edge-tts → afplay (로컬 재생)
  └─ POST ntfy(설정돼 있으면) → 폰 푸시
```

### 3.4 모바일 ↔ 데스크톱 핸드오프
```
데스크톱:  tmux 세션 'dev' 생성 (bin/fsh)
  ↓ (같은 OS의 tmux server에 등록됨)
데스크톱 iTerm:  tmux attach -t dev
모바일 브라우저:  GET /?...#tmux=dev
  → frontend/js/term/boot.js가 hash 파싱
  → apiFetch POST /api/tmux/attach {name:"dev"}
  → 서버: pty.fork() → exec "tmux attach -t dev"
  → WebSocket으로 화면 중계(?e2e=1이면 E2E 암호화)
```

**포인트**: 양쪽이 **같은 tmux 세션의 다른 클라이언트**일 뿐. 버퍼·스크롤백·
프로세스 모두 공유.

### 3.5 idle 감지 → 푸시 (OutputWatcher)
```
PTY 출력 → output_watcher.feed_output()
  → 버퍼에 쌓임
  → idle_timeout(3s) 초과 시
  → summary 생성 → TTS 합성
  → notify.send() (ntfy/Telegram 병렬)
```

### 3.6 E2E 암호화 WebSocket 핸드셰이크 (D3)
```
클라이언트(?e2e=1): frontend/js/term/e2e.js의 wrapE2E()
  ← server/crypto_channel.py가 e2e-hello 전송: 안정적인 Ed25519 identity
    키로 서명된 임시(ephemeral) 공개키
  → 클라이언트가 서명 검증, 호스트별로 identity 키를 TOFU 핀닝(localStorage),
    나중에 키가 바뀌면 경고 + 명시적 재신뢰 요구
  → 양쪽이 공유 키(X25519) 도출 → 매 프레임을 NaCl SecretBox로 암호화
```
cloudflared/Tailscale 경로에서의 수동 도청과 능동 MITM 둘 다 방어한다.
대가는 클라이언트가 첫 접속 시 identity 키를 신뢰해야 한다는 것 — §6 참고.

### 3.7 라이브 프리뷰 그리드 → 에이전트 상태
```
frontend/js/agent/preview.js가 보이는 카드마다 /ws-preview/{tmux_name} 연결
  (server/preview.py — 읽기 전용, 진짜 tmux 클라이언트로 안 잡힘)
frontend/js/agent/status.js가 /ws-agent 하나만 연결
  (server/agent_status.py — Claude Code Pre/Post/Stop 훅이 /api/agent/event로
   먹인다. 훅이 pane을 직접 알려주지 않아 cwd로 매칭 — CLAUDE.md의 캐비어트 참고)
```

---

## 4. 확장 포인트

새 기능을 붙일 때 어디를 건드려야 하는지.

### 4.1 새 STT 엔진 추가
- `server/voice_handler.py`(또는 데몬 경로는 `server/voice/stt.py`)의 우선순위 리스트에 삽입
- mlx-whisper → faster-whisper 순서 참고

### 4.2 새 TTS 엔진 추가
- `server/voice_handler.py` synthesize() 함수의 fallback 체인
- 바이트 반환 or 직접 재생 두 경로 모두 지원

### 4.3 새 푸시 알림 채널 (예: Discord, Slack)
- `server/notify.py`에 `_send_xxx()` 함수 추가
- `is_configured()` 및 `send()`에서 병렬 task 리스트에 포함
- 환경변수 규칙: `VT_XXX_TOKEN` / `VT_XXX_WEBHOOK`

### 4.4 새 CLI 서브커맨드
- `bin/fsh`의 main switch에 케이스 추가
- 함수명 규칙: `cmd_<이름>()`
- help 섹션 문자열에 한 줄 추가([CLI.ko.md](./CLI.ko.md)도)

### 4.5 새 AI 에이전트 (Claude 외)
- 터미널 자체는 **별도 래퍼 불필요** — `fsh agent <name>`이 이미 일반화돼
  있고, 사용자가 그냥 tmux 안에서 `aider`/`codex`/등을 실행하면 음성·모바일이
  모두 자동으로 동작한다(범용 tmux 주입 설계의 이점).
- 에이전트 상태 감지(배지, 작업중/완료 상태)를 붙이려면
  `server/agent_detector.py`의 매니페스트 방식 룰을 확장.
- Claude Code Stop hook과 유사한 완료 알림이 필요하면 해당 도구의 종료
  이벤트를 `tts_hook.sh` 스타일로 작성.

### 4.6 새 엔드포인트
- `server/routes/` 아래 적절한 모듈에 라우트 추가(완전히 새로운 카테고리면
  새 모듈 + `main.py`에 `app.include_router(...)`)
- 토큰/비밀번호 인증은 middleware가 자동 처리(`/sw.js`, `/manifest.json` 등
  화이트리스트 제외)
- 위험한 작업은 session_id로 제한, [API.md](./API.md) 갱신
- 프런트엔드가 필요하면 해당 ES 모듈에서 `vtFetch`/`apiFetch` 호출 추가 —
  중앙 "API 클라이언트" 파일은 더 이상 없고, 각 기능 모듈이 자기 호출을
  직접 소유한다.

### 4.7 새 원격 접속 경로 (D9: Tailscale + SSH 예시)
- 원격 데스크톱/브라우저가 막힌 환경(회사망 등)에서도 tmux는 "단일 진실의
  원천"이라 **새 클라이언트 종류를 추가하는 것만으로** 접속 경로를 늘릴 수
  있다 — SSH도 web/voice와 동급의 다섯 번째 클라이언트일 뿐, 별도 프로토콜
  구현이 필요 없다(그냥 `tmux -L fsh attach`).
- 네트워크 정책에 새 CIDR 대역을 추가하려면 `network_access.py`의
  `_expand_keyword()` + `network_mode_to_spec()`에 키워드/모드 추가
  (Tailscale은 `tailscale` → CGNAT `100.64.0.0/10`).
- 대역 자체의 상태 조회(설치/실행/자기 IP)는 `tunnel.py`(Cloudflare)와 동일한
  패턴으로 독립 모듈에 분리(`server/tailscale.py`) — `network_access.py`는
  CIDR 판단만, 상태 조회는 별도 모듈이 담당하는 게 관례.
- 서버가 자연히 못 보는 클라이언트(순수 SSH 등)의 접속을 알고 싶으면 tmux
  훅(`client-attached`/`client-detached`)으로 이벤트를 잡아
  `/api/notify/client-event` 같은 내부 전용 엔드포인트에 POST → 기존
  `notify.py` 브릿지 재사용. `bin/fsh`가 옵트인 환경변수로 훅을 등록/해제하는
  패턴을 따르면 기본 동작을 안 건드리고 추가 가능.

### 4.8 프런트엔드 모듈 분할/추가 (2.0 이후)
- 새 기능 모듈은 파일이 여러 개로 늘어날 것 같으면 주제별 디렉토리
  (`js/<주제>/`) 아래, 작고 자기완결적이면 최상위 `js/<이름>.js`로
  (패턴은 `search.js`/`ports.js` 참고).
- 공유 상태/유틸리티는 `core/*.js`에서 import — `API_BASE`/토큰 처리/세션
  상태를 로컬에서 다시 만들지 않는다.
- 두 모듈이 서로를 불러야 하면, 모든 교차 호출이 함수 본문 안에서만
  일어나는 한(모듈 평가 시점에 안 일어나면) 순환 `import`도 괜찮다
  (`js/panels/viewer/shell.js` 상단 주석의 근거 참고). 순환을 피하려고
  억지로 한쪽 방향 분할을 강요하지 않는다.
- `js/voice/*.js`에서는 **절대** mutable state를 가진 `core/*.js` 모듈
  (`store.js`, `dom.js`)을 import하지 않는다 — 그 번들은 완전히 독립적으로
  빌드되므로, 거기서 import하면 그 모듈의 상태를 "공유"하는 게 아니라
  조용히 "복제"해버린다(§2 `frontend/` 절과, 실제로 이 때문에 났던 버그
  참고). 공유가 필요한 실시간 값은 대신 `window.X`로 읽는다.

---

## 5. 실행 시 프로세스 맵

```
$ fsh start
  ├─ uvicorn server.main:app  (port 7777)                [서버]
  ├─ cloudflared tunnel --url ...                        [터널]
  ├─ python -m server.voice.daemon                       [음성 데몬]
  ├─ python server/tunnel_watchdog.py                    [터널 워치독]
  └─ tmux -L fsh server (새 session: dev)                [tmux]
      └─ zsh (또는 claude --resume)                      [작업 셸]

$ fsh manage                                             [server/tui/, 독립 실행]
```

PID는 `/tmp/vt-pids/{server,tunnel,voice}.pid`에 저장됨. `fsh stop`이 모두 정리.

---

## 6. 보안 모델 (현재 상태)

| 계층 | 메커니즘 | 한계 |
|---|---|---|
| 전송 | cloudflared HTTPS 터널 | — |
| 전송 (대안) | Tailscale WireGuard VPN + IP 화이트리스트 (D9, `--network tailscale`) | Tailscale 자체 신뢰 필요, tailnet ACL 별도 관리 |
| 사람 인증 | 비밀번호(scrypt 해시) → 24h HMAC 서명 세션 쿠키(`server/auth.py`) | — |
| 기계 인증 | `VT_AUTH_TOKEN`을 `?token=` 또는 `Authorization: Bearer`로 — 데몬/QR/URL용 | 첫 사용 시 쿠키로 교환되고 URL에서 제거됨(Phase 9 #8) |
| 기기 게이트 | 처음 보는 기기용 TOTP 게이트(옵트인, `fsh otp setup`), 이후 90일 `vt_device` 쿠키 | `fsh otp setup`으로 명시 연동 전까지 완전히 비활성 — 그동안 기기 등록은 조용히 쌓임 |
| 크로스사이트 | `OriginGuardMiddleware`가 인증보다 먼저 Origin이 자기 자신 아닌 요청/WS 전부 거부 | 비밀번호/OTP만으로는 못 막는 유일한 경로 |
| WebSocket 인증 | 미들웨어가 accept 전 검증 | — |
| 세션 ID | `secrets.token_urlsafe(12)` — 16자, ~96비트 | — |
| **E2E 암호화** | **구현됨**(`?e2e=1`) — X25519 + Ed25519 identity 서명 + NaCl SecretBox, TOFU 핀닝(§3.6) | 옵트인, 기본값 아님. identity 키 교체 시 사용자의 명시적 재신뢰 필요 |
| 코드 뷰어 | 고정 루트(`VT_BROWSE_ROOTS`) + `Path.resolve()`/`is_relative_to`(`startswith` 금지) + 거부 목록, 전부 한 곳(`fsguard.py`); 읽기 전용, 쓰기 API 없음 | 공개 터널로 노출되므로 여기가 주된 blast-radius 통제 지점 |
| 포트 대시보드 | FarShell 서버 자신이나 `cloudflared`/`tailscaled`/`sshd`는 종료 불가; 다른 사용자 프로세스도 불가(sudo 미사용); 종료 직전 port→pid 재확인으로 PID 재사용 오류 방지 | `expose`는 포트를 공인 인터넷에 연다 — `confirm:true` + `VT_NETWORK_MODE=all` 필요 |
| 명령 안전성 | `safe_mode.py`가 설정 가능한 위험 명령 목록을 사전 차단 | 패턴 매칭 기반 best-effort, 샌드박스 아님 |
| 업로드 | `/tmp/vt-uploads/` 격리 | 디스크 쿼터 없음 |
| 접속 가시성 | `VT_NOTIFY_CLIENT_EVENTS=1` → tmux client-attached/detached push (D9) | 기본 OFF, `who` 기반 원격 호스트 추출은 best-effort |

---

## 7. 현황

무엇이 언제 나왔는지는 [CHANGELOG.md](./CHANGELOG.md)가 단일 진실의
원천이다 — 이 문서는 로드맵이나 "완료/남음" 목록을 일부러 따로 두지 않는다
(예전에 그렇게 했다가 실제와 어긋났다). §2 전반에서 언급하는 프런트엔드
재구조화(2026년 9월 ES 모듈 전환)의 상세 이력은 로컬 전용
`docs/plan-2.0/10-frontend-restructure.md` 참고(gitignore 대상, GitHub엔
없음).
