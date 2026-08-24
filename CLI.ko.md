# fsh CLI 레퍼런스

`fsh`는 터미널 어디서든 실행 가능한 단일 진입점입니다. 이 문서는 전체 명령/옵션의
상세 레퍼런스입니다 — 개요는 [README.md](./README.md), 개념 설명은 `fsh help concepts`를
참고하세요.

---

## 명령 목록

| 명령 | 설명 |
|------|------|
| `fsh voice` | 음성 모드 — 서버 + Voice Daemon 백그라운드 시작 (노션 등 다른 작업 중에도 사용 가능) |
| `fsh clip` | 클립보드 동기화 데몬 — 맥 클립보드 변경을 웹으로 push (OSC52로 못 잡는 터미널 밖 복사 보완) |
| `fsh mobile [옵션]` | 모바일 접속 URL + QR 코드 출력 |
| `fsh start` | 전체 시작 — 서버 + 터널 + 음성 데몬 |
| `fsh stop [--purge]` | 종료 — `--purge`는 tmux 세션까지 완전 종료 |
| `fsh status` | 서버·터널·Voice Daemon·tmux 상태 확인 |
| `fsh manage` | TUI 관리 도구 — 세션 목록/rename/kill/attach + 핫키/상태 조회 |
| `fsh attach [name]` | 임의 tmux 세션을 새 OS 터미널 창에 attach |
| `fsh voice-target [name\|--auto]` | Voice Daemon 타깃 세션 lock/해제 |
| `fsh queue [하위명령]` | 프롬프트 큐 — 작업 중 지시를 쌓아뒀다 순차 투입 (아래 [프롬프트 큐](#프롬프트-큐-fsh-queue) 참고) |
| `fsh hotkey [list\|set\|reset\|disable]` | 핫키 조회/변경 |
| `fsh password [clear]` | 웹 로그인 비밀번호 설정(해시만 저장) / `clear`로 해제 |
| `fsh otp [status\|setup\|disable]` | 새 기기 등록 시 OTP 요구 — `setup` 전까지 완전 비활성 |
| `fsh device [list\|revoke <id>]` | 등록된 기기 조회 / 폐기 (폰 분실 시 세션까지 함께 무효화) |
| `fsh help <topic>` | 토픽별 도움말 — `concepts`/`voice`/`hotkeys`/`target`/`troubleshoot`/`webui`/`ssh`/`tunnel-hook` |
| `fsh claude` | 새 터미널 창에 `tmux dev` + `claude --resume` 오픈 |
| `fsh agent <name>` | claude/codex/aider/gemini 시작 (일반화) |
| `fsh handoff mobile` | 현재 tmux 세션을 폰으로 넘김 (QR + `#tmux=`) |
| `fsh handoff desktop` | 폰 세션을 맥 터미널로 가져옴 |
| `fsh template [save\|apply\|list\|rm] <name>` | CLAUDE.md 템플릿 관리 |
| `fsh popup <action>` | tmux 3.2+ popup으로 빠른 호출 |
| `fsh run "..."` | headless `claude -p` 백그라운드 실행 + TTS 알림 |
| `fsh tunnel expose <port> "이름"` | 다른 로컬 포트를 별도 Cloudflare 터널로 공개 |
| `fsh tunnel unexpose <port>` | 해당 포트 터널 종료 |
| `fsh tunnel list` | 열려 있는 터널 전부 (메인 + 추가 포트) |
| `fsh tunnel hook` | URL 변경 훅 확인 + 즉시 실행 (자세히: `fsh help tunnel-hook`) |
| `fsh tunnel restart` | 좀비 재연결(응답 없음) 상태여도 강제로 새 터널 기동 + 훅 재실행 |
| `fsh tunnel watchdog` | 좀비 재연결 자동 감지 데몬 상태 확인/시작 (평소엔 자동 기동) |
| `fsh ssh [session]` | Tailscale + SSH로 tmux 세션 직접 접속 — 회사망 등 화면 원격이 막힌 환경 (자세히: [아래](#tailscale--ssh-원격-접속)) |
| `fsh doctor` | 설치/환경 진단 — 아래 [점검 항목](#fsh-doctor-점검-항목) 참고 |
| `fsh install-profiles [--dry-run]` | 터미널 앱 profile 자동 등록 (iTerm2 Dynamic Profile + 기타 snippet) |
| `fsh shell-init [zsh\|bash\|fish\|pwsh]` | 셸별 안전 통합 스니펫 출력 (`eval "$(fsh shell-init zsh)" >> ~/.zshrc`) |

> 지원 OS: macOS / Linux (X11) / WSL2 (Linux로 동작). Windows 네이티브는 미지원.

---

## `fsh mobile` 옵션

```bash
fsh mobile --e2e                       # X25519 핸드셰이크 + NaCl SecretBox E2E 암호화
                                       #   (서버 장기 신원키로 서명 — TOFU pinning, 첫 접속 신뢰)
fsh mobile --safe                      # 위험 명령(rm -rf /, sudo 등) 사전 차단
fsh mobile --network <mode>            # localhost | lan | tailscale | all (기본값)
fsh mobile --force                     # 인증(비밀번호/토큰) 미설정이어도 공개 터널 강행 — 비권장
```

`--network all`(기본값)로 공개 터널을 열려면 `fsh password` 또는 `VT_AUTH_TOKEN`으로
인증을 먼저 설정해야 합니다 — 미설정 상태면 실행이 거부됩니다(무인증 원격 코드
실행을 막기 위함). 위험을 감수하고 강행하려면 `--force`를 명시하세요.

`tailscale` 모드는 Cloudflare Tunnel 없이 자신의 tailnet IP로만 서버를 열고,
네트워크 정책도 tailnet CIDR(`100.64.0.0/10`)+localhost로만 제한합니다.

---

## 프롬프트 큐 (`fsh queue`)

에이전트가 작업 중일 때 지시를 쌓아뒀다 순차 투입합니다. 음성 모드와 짝을 이루는
기능 — 지금은 작업 중에 말하면 씹히는데, 큐가 있으면 걸어가며 여러 개를 던져놓고
순서대로 실행시킬 수 있습니다.

```bash
fsh queue list                  # 큐 목록
fsh queue add "다음 지시" [세션]  # 큐에 추가 (상한 50개)
fsh queue run                   # 수동 드레인 — 한 건 투입
fsh queue rm <id>                # 항목 삭제 (id=all이면 전체 비우기)
fsh queue unblock <id>          # safe_mode에 막힌 항목 재개
fsh queue clear                 # 전체 비우기
```

자동 투입은 **Claude Code의 Stop 훅에서만** 걸립니다. codex/aider/gemini는 훅이
없어 `fsh queue run` 또는 웹 UI의 "지금 실행"으로 수동 투입해야 합니다. 투입 전
관문 4개: 유예 시간(사용자가 직접 타이핑을 시작했을 수 있음) → safe_mode(위험
명령이면 투입하지 않고 blocked로 남김) → 타깃 pane 생존 확인 → 한 번에 한 건.

---

## `fsh doctor` 점검 항목

| # | 항목 | 내용 |
|---|------|------|
| 1 | Python | 경로·버전 확인 |
| 2 | venv | `.venv` 또는 legacy conda env |
| 3 | core packages | fastapi, uvicorn |
| 4 | voice packages | faster-whisper, edge-tts, sounddevice |
| 5 | tmux | 설치 여부 및 버전 |
| 6 | cloudflared | 원격 접속 도구 |
| 7 | ffmpeg | 모바일 음성 디코딩 |
| 8 | port | VT_PORT 사용 상태 |
| 9 | fsh CLI | `~/.local/bin/fsh` 심링크 (`vt`도 하위 호환 심링크로 동작) |
| 10 | PATH | `~/.local/bin` 포함 여부 |
| 11 | `.vt.env` | 설정 파일 존재 여부 |
| 12 | 인증 | 비밀번호/토큰 설정 여부 |
| 13 | 터미널 앱 | 감지된 앱 목록 + 현재 `TERM_PROGRAM` |
| 14 | Tailscale | 설치/연결 상태 (D9) |

---

## Tailscale + SSH 원격 접속

회사망이 화면 공유(크롬 원격 데스크톱·TeamViewer·RDP/VNC)를 막아둔 경우가 있습니다.
Tailscale(WireGuard 기반 VPN 메시)은 UDP 홀펀칭 또는 443 DERP 릴레이 폴백으로
동작해 이런 방화벽도 대부분 통과합니다. 화면 전체가 아니라 **터미널만** 필요하다면
Tailscale + SSH로 집 맥북의 tmux 세션에 직접 붙는 쪽이 화면 원격보다 가볍고 빠릅니다.

tmux 세션이 단일 진실의 원천이므로, SSH도 데스크톱 iTerm·모바일 PWA·Voice Daemon과
같은 하나의 클라이언트일 뿐입니다 — 회사에서 SSH로 붙어도 집에서 보던 것과 완전히
같은 화면·스크롤백·실행 중인 Claude 세션을 그대로 이어받습니다.

```bash
# 맥북에서 (Tailscale이 이미 tailscale up으로 연결돼 있어야 함)
fsh ssh                   # 세션 'dev'로 접속하는 명령을 안내 (복사해서 회사 노트북에서 실행)
fsh ssh mysession         # 다른 세션 이름 지정
fsh ssh --user alice      # 원격 로그인 계정 지정 (기본: 현재 계정)
fsh ssh --add-key "ssh-ed25519 AAAA... user@laptop"   # 공개키 등록
```

이 경로는 순수 텍스트 SSH라 브라우저 마이크/스피커를 못 씁니다 — 음성 대신 키보드로
직접 입력합니다. 완료·idle 알림은 기존 push 브릿지로 그대로 받고, `VT_NOTIFY_CLIENT_EVENTS=1`을
설정해두면 SSH 접속/해제도 push로 알림받을 수 있습니다.

| 접속 방식 | 필요한 것 | 용도 |
|---|---|---|
| `fsh ssh` | Tailscale + (선택) SSH 키 | 순수 터미널, vim/IDE 등 키 입력 위주 작업 |
| `fsh mobile --network tailscale` | Tailscale + 브라우저 | 폰에서 음성 입력, 터치 조작 |
| `fsh mobile` (기본, `--network all`) | 인증(비밀번호/토큰) | Tailscale 없는 완전 외부 기기 |

자세한 내용: `fsh help ssh`.

---

## 자동 오픈 동작

`fsh voice` / `mobile` / `start` 실행 시:
- 현재 쓰는 터미널 앱 자동 감지 → 새 창 오픈 → `tmux new -A -s dev 'claude --resume'` 실행
- 지원 앱: iTerm2, Ghostty, WezTerm, Kitty, Alacritty, Warp, Terminal.app
- 이미 tmux 안에 있으면 새 창 없이 현재 창에서 계속 (`$TMUX` 체크, 멱등성 보장)

## 설치 후 통합 (선택)

새 터미널 창을 열면 자동으로 `tmux -L vt new -A -s dev` 진입하도록 통합. 둘 중 하나를 선택.

### 방식 A — 터미널 profile 자동 등록 (권장)

```bash
fsh install-profiles --dry-run   # 변경 미리보기
fsh install-profiles             # 실제 적용
```

iTerm2는 Dynamic Profile 자동 등록. Ghostty / WezTerm / Kitty / Alacritty / Windows
Terminal / Terminal.app은 config snippet 안내 출력(복사·붙여넣기). p10k instant
prompt와 충돌 없음.

### 방식 B — 셸 init (SSH 원격, profile 불가 환경)

```bash
echo 'eval "$(fsh shell-init zsh)"' >> ~/.zshrc      # zsh
echo 'eval "$(fsh shell-init bash)"' >> ~/.bashrc    # bash
fsh shell-init fish >> ~/.config/fish/config.fish    # fish
fsh shell-init pwsh >> $PROFILE                       # PowerShell
```

생성되는 스니펫은 5중 TTY 가드 포함(`interactive` + TTY + `$TMUX` 비어있음 + IDE
임베디드 셸 차단 + tmux 존재). p10k instant prompt 활성 zsh에서도 콘솔 출력 0건.

### 단일 tmux 서버 원칙

`fsh` CLI · server · Voice Daemon · hook이 모두 `-L vt` 격리 소켓을 사용 → 모든
클라이언트(데스크톱·모바일·Voice Daemon)가 같은 세션을 공유. 사용자의 기존
`tmux ls` 세션과는 자동 분리됩니다.
