# fsh CLI 레퍼런스

`fsh`는 터미널 어디서든 실행 가능한 단일 진입점입니다. 이 문서는 전체 명령/옵션의
상세 레퍼런스입니다 — 개요는 [README.ko.md](./README.ko.md), 개념 설명은 `fsh help concepts`를
참고하세요.

---

## 명령 목록

터미널 어디서든 `fsh` 명령으로 FarShell을 제어합니다:

```bash
fsh start [--network <범위>] [--safe] [--force] [--voice]
                       # 서버와 접속 경로 시작 (--voice: 음성 데몬도 함께)
fsh restart [--voice]  # 서버만 재기동 — 터널을 유지해 접속 주소가 안 바뀜
fsh stop [--purge] [--keep-tunnel]
                       # 종료 (--purge: tmux 세션까지 완전 종료,
                       #       --keep-tunnel: 공개 입구는 남김)
fsh status             # 현재 상태 확인
fsh mobile [--e2e] [--session <이름>] [--temp]
                       # 폰 접속 URL + QR (서버가 떠 있어야 함, --e2e: 페이로드 암호화)
fsh manage             # TUI 관리 도구 (세션/타깃/핫키/상태)
fsh attach [name]      # 임의 tmux 세션을 새 창에 attach
fsh voice              # 음성 모드 (백그라운드, 다른 앱 사용 중에도 사용)
fsh voice-target [name|--auto]  # 음성 daemon 타깃 lock/해제
fsh clip               # 클립보드 동기화 데몬 (맥 클립보드 변경 → 웹, OSC52 보완)
fsh queue [list|add "내용" [세션]|run|rm <id>|unblock <id>|clear]  # 프롬프트 큐
fsh files [ls|add <path> [--share ttl] [--pin]|rm <id>|share <id> [--ttl] [--pin] [--once]|unshare <id>|insert <id>]  # 파일 저장소 + 공유 링크
fsh host [list|pair|add <url> --ticket <t>|ping <id>|rm <id>|rename <id|self> <이름>|allow-control <id>|deny-control <id>|allow-agent <id>|deny-agent <id>|log|revoke-all|help]  # 다른 맥의 FarShell 페어링
fsh msg [log [대화id]|agents|send <[호스트/]세션> "내용" [--reply <id>]]  # 에이전트 간 메시지
fsh mcp                         # 에이전트용 MCP 서버(stdio): list_agents / send_message / read_messages + 공유 세션(list_shared_sessions / read_session / send_to_session)
fsh worktree [list|add <이름> [--base b] [--ports] [--copy-modules] [--agent claude]|rm <이름> [--force]|open <이름>]  # git 워크트리
fsh git-account [list|add --provider github|gitlab [--host H] --token-stdin|rm <id>|bind <repo> <id>]  # git 계정 저장소 (저장한 자격증명은 push에 사용되지 않음)
fsh hotkey [list|set|reset|disable]  # 핫키 조회/변경
fsh hooks [status|install|uninstall|dedupe [--dry-run]]  # Claude Code·Codex 훅 등록(상태 배지·큐·TTS의 전제); dedupe는 Codex 앱이 ~/.codex/hooks.json에 복제한 FarShell 훅 제거
fsh pane report [--state ...] [--agent ...]  # 이 pane의 상태 보고(훅 없는 에이전트용)
fsh clauth [status|which]  # 사용량 조회(읽기 전용, clauth 미설치면 숨김)
fsh usage [list|add --model <이름> --tokens <N> --seconds <N>]  # 누적형 사용량 기록/조회 (로컬 LLM 등, 한도 없음)
fsh password [clear]   # 웹 로그인 비밀번호 설정(해시 저장) / clear=해제
fsh otp [status|setup|disable]   # 새 기기 등록 시 OTP 요구 (setup 전까지 완전 비활성)
fsh device [list|rename <id> <별명>|revoke <id>|prune]  # 등록된 기기 조회 / 이름 변경 / 폐기(폰 분실 시 세션까지 함께 무효)
fsh help <topic>       # concepts/voice/hotkeys/target/troubleshoot
fsh template [save|apply|list|rm] <name>  # CLAUDE.md 템플릿 저장/적용 관리
fsh popup <action>     # tmux 3.2+ popup으로 fsh 명령 빠른 호출
fsh run "..."          # headless `claude -p` 백그라운드 실행 + 완료 시 TTS 알림
fsh handoff mobile     # 현재 tmux 세션을 폰으로 넘김 (QR + #tmux=)
fsh handoff desktop    # 폰 세션을 맥 터미널로 가져옴
fsh tunnel expose 3000 "앱 이름" [--mode public|session|pin] [--ttl 1h|24h|7d|30d] [--force]
                                   # 로컬 포트 공유. 기본 session: 로그인한 기기만 접근
                                   # --auto-renew/--no-auto-renew가 없으면 자동 재노출 여부를 묻는다(터미널이 아니면 끔)
fsh tunnel unexpose 3000          # 해당 포트 터널과 공유 정책 종료
fsh tunnel pin 3000               # PIN 재발급 (URL 유지)
fsh tunnel mode 3000 session      # 접근 모드 변경 (URL 유지, 재인증 필요)
fsh tunnel auto 3000 on|off       # 자동 재노출 켜기/끄기 (만료 시 연장, 터널이 사라지면 다시 엶. 자격 유지)
fsh tunnel list                   # 전체 터널, 접근 모드와 PIN 실패 횟수
fsh tunnel hook                   # URL 변경 훅 확인 + 즉시 실행 (fsh help tunnel-hook)
fsh tunnel restart                # 좀비 재연결(응답 없음) 상태여도 강제로 새 터널 기동 + 훅 재실행
fsh tunnel watchdog               # 좀비 재연결 자동 감지 데몬 상태 확인/시작 (평소엔 fsh start/voice/mobile가 자동 기동)
fsh ssh [session]      # Tailscale + SSH로 tmux 세션 직접 접속 명령 안내
fsh doctor             # 설치/환경 진단 (Linux 항목 포함)
fsh terminal           # fsh가 창을 열 때 쓰는 메인 터미널 확인/선택
fsh install-profiles   # 터미널 앱 profile 자동 등록 (iTerm2 Dynamic Profile + 기타 snippet)
fsh setup-keybind [key] [action]  # tmux popup 키바인딩 등록 (기본: prefix+v → status)
fsh shell-init zsh     # 셸 init 스니펫 출력 (출력한 스니펫을 ~/.zshrc에 추가)
```

> **지원 OS**: macOS / Linux (X11) / WSL2 (Linux로 동작). Windows 네이티브는 미지원.

**단일 tmux 서버 원칙:** fsh CLI · server · Voice Daemon · hook이 모두 `-L fsh` 격리 소켓 사용(기본 소켓 이름 `fsh`). Voice Daemon은 `VT_TMUX_SOCKET` 환경변수로 오버라이드 가능. 사용자 기존 `tmux ls`와 분리됨.

**터미널 창 열기:** `fsh attach`, `fsh handoff desktop`이 **메인 터미널**(아래 `fsh terminal`)에 새 창을 열고 그 안에서 tmux에 붙습니다. `voice` / `mobile` / `start`는 창을 열지 않습니다. 이미 tmux 안이면 새 창을 열지 않고, 창이 이미 붙어 있는 세션은 두 번 열지 않습니다.

**다른 앱 사용 중 음성 코딩 워크플로:**
1. `fsh voice` → 백그라운드 시작, 터미널에서 `tmux -L fsh new -A -s dev` 로 들어가 `claude --resume` 실행
2. 새 창의 resume 목록에서 현재 대화 선택 → 이후 음성/모바일이 그 Claude로 연결됨
3. 원래 창은 그대로 두고 다른 앱에서 작업
4. Ctrl+Shift+M → 말하기 ("git status") → tmux dev에 자동 입력
5. `fsh stop` → 종료

> 이미 tmux 안에서 `fsh` 명령을 부르면 새 창을 열지 않습니다 (`$TMUX` 체크).
> 창을 열지 못하면(앱 없음, macOS 자동화 권한 거부) 수동 명령(`tmux -L fsh new -A -s dev 'claude --resume'`)이 출력됩니다.


## `fsh start` / `fsh mobile` 옵션

`fsh start`가 **누가 접속할 수 있는지**를 정하고, `fsh mobile`은 폰으로 접속할 URL과 QR을 보여줍니다.

```bash
fsh start --network <범위>             # localhost | lan | tailscale | all (기본값)
fsh start --safe                       # 위험 명령(rm -rf /, sudo 등) 사전 차단
fsh start --force                      # 인증(비밀번호/토큰) 미설정이어도 공개 터널 강행 — 비권장
fsh mobile --e2e                       # X25519 핸드셰이크 + NaCl SecretBox E2E 암호화
                                       #   (서버 장기 신원키로 서명 — TOFU pinning, 첫 접속 신뢰)
fsh mobile --temp                      # 이 QR로 등록한 기기는 1일 뒤 만료
fsh mobile --session <tmux세션>        # 폰이 열릴 때 이 tmux 세션을 바로 보기 (fsh handoff mobile과 같음)
```

`--network all`(기본)로 공개 터널을 열려면 먼저 `fsh password` 또는 `VT_AUTH_TOKEN`으로 인증을
설정해야 합니다 — 설정하지 않으면 `fsh start`가 거부합니다(인증 없는 원격 코드 실행 방지).
위험을 알고도 진행하려면 `fsh start --force`.

예전엔 이 옵션들이 `fsh mobile`에 있었습니다. `mobile`에 `--network`/`--safe`/`--force`를 주면
`fsh start`를 대신 실행하고 안내를 출력합니다. 자세히: `fsh help access`.

포트 공유의 `session` 모드는 「처음 열기」 링크로 접속합니다. `public`은 인증 없이
열리며, 원격 제어 포트는 `--force`가 필요합니다. 기간이 끝난 공유는 1분 안에 종료됩니다.
자세히: `fsh help tunnel`.

## 에이전트 간 메시지 (`fsh msg`, `fsh mcp`)

한 에이전트가 다른 세션(이 맥 또는 페어링된 맥)의 에이전트에게 메시지를 보냅니다.
전달은 프롬프트 큐를 탑니다 — 받는 에이전트의 **작업이 끝난 뒤**(Claude Code·Codex의
stop 훅), 이미 쉬고 있다고 확실히 알면 바로 입력됩니다. 훅이 없는 에이전트는
`fsh queue run` / "지금 실행"까지 큐에 남습니다.

```bash
claude mcp add farshell -- fsh mcp      # 에이전트에게 도구 주기 (Codex/agy도 같은 명령 등록)
fsh msg agents                          # 보낼 수 있는 곳
fsh msg send M5/api "테스트 돌려줘"       # 셸에서 보내기 (MCP 없는 에이전트도 가능)
fsh msg log                             # 기록과 전달 상태
fsh host allow-agent <id>               # 받는 맥에서 — allow-control도 함께 필요
```

제한: 대화당 10번 왕복, 보내는 맥당 분당 20건, 4000자. 받는 에이전트에게는 "다른
에이전트가 보낸 요청"이라는 한 줄 머리글이 붙습니다.

**다른 맥이 할 수 있는 일 바꾸기 (`fsh host allow-*` / `deny-*`).** 서버가 `VT_AUTHZ_ENFORCE=peer` 로 떠 있으면 이 명령들은 `~/.vt/hosts.json` 을 직접
쓰지 않고 unix socket 으로 서버에 요청합니다. `deny-control` / `deny-agent` 는 접근을 줄이므로 바로 적용되고 감사 기록에 남습니다.
`allow-control` / `allow-agent` 는 접근을 늘리므로 승인 요청만 만들어지고, 웹 승인함에서 사람이 승인해야 하며 이 명령으로는 승인할 수 없습니다.
요청을 만들면 종료 코드 0 이지만 아직 아무것도 바뀌지 않았다고 알려 줍니다. 그 설정이 꺼져 있거나 소켓이 없거나 옛 서버면 예전처럼
`hosts.json` 에 바로 씁니다. `control` 은 그 맥이 이 맥의 터미널에 명령을 칠 수 있다는 뜻입니다 — 원격 셸과 같습니다. `fsh host help` 로 볼 수 있습니다.

공유 세션용 MCP 도구는 `list_shared_sessions`, `read_session`, `send_to_session`입니다.
사용자가 웹에서 공유한 세션만 접근할 수 있으며, 첫 접근은 브라우저의 사용자 승인이
필요합니다. 읽기·쓰기 권한은 따로 승인합니다. 자세히: `fsh help agent-state`.

## 프롬프트 큐 (`fsh queue`)

에이전트가 작업 중일 때 다음 지시를 큐에 저장하고, 완료 후 한 건씩 투입합니다.

```bash
fsh queue list                  # 큐 목록
fsh queue add "다음 지시" [세션]  # 큐에 추가 (상한 50개)
fsh queue run                   # 수동 드레인 — 한 건 투입
fsh queue rm <id>                # 항목 삭제 (id=all이면 전체 비우기)
fsh queue unblock <id>          # safe_mode에 막힌 항목 재개
fsh queue clear                 # 전체 비우기
```

자동 투입은 **Claude Code·Codex의 완료 훅**으로 시작됩니다. aider/agy는 관리형 훅이
없어 `fsh queue run` 또는 웹 UI의 "지금 실행"으로 수동 투입해야 합니다. 투입 전
관문 4개: 유예 시간(사용자가 직접 타이핑을 시작했을 수 있음) → safe_mode(위험
명령이면 투입하지 않고 blocked로 남김) → 타깃 pane 생존 확인 → 한 번에 한 건.

---

## `fsh` 동작을 바꾸는 설정

설정은 전부 `~/.vt.env`(gitignored, 머신별)에 있고, 키 목록과 설명은
[`config/vt.defaults.env`](./config/vt.defaults.env)에 있다 — 그 파일이 목록이다.
쓸 때는 `fsh` 명령이나 `lib/vt_env.sh`를 쓴다. `echo`/`sed`로 직접 건드리지 않는다
(AGENTS.md 계약 5). 이 맥에 어떻게 도달하는지를 정하는 것들만 추리면:

| 키 | 하는 일 |
|-----|--------------|
| `VT_TUNNEL_PROVIDER` | `cloudflare`(기본) · `ngrok` · `none` — 공개 입구를 누가 담당하는가. `start`·`stop`·`status`·`mobile`·`handoff`·`tunnel expose`가 전부 따른다 |
| `VT_NGROK_DOMAIN` | ngrok 예약 도메인. 없으면 실행할 때마다 임시 주소가 잡혀서 고정 주소라는 목적이 사라진다 |
| `VT_TUNNEL_NAME` + `VT_TUNNEL_HOSTNAME` | Cloudflare 명명 터널(본인 도메인이 Cloudflare에 있어야 한다) |
| `VT_NETWORK_MODE` | `localhost` · `lan` · `tailscale` · `all` — 애초에 누가 포트에 닿을 수 있는가 |
| `VT_TRUST_PROXY` | 신뢰하는 프록시의 `X-Forwarded-For`를 믿는다. 기본 꺼짐 — 직결 노출 상태에서 켜면 클라이언트가 자기 IP를 위조할 수 있다 |

**보안 경계값은 셸 환경변수가 아니라 설정 파일을 먼저 읽는다** — 낡은 `export` 하나가
좁혀놓은 열람 경계를 계속 열어둔 적이 있다. 바로잡으면 `fsh status`·`fsh doctor`가 말해준다.

## `fsh doctor` 점검 항목

| # | 항목 | 내용 |
|---|------|------|
| 1 | Python | 경로·버전 확인 |
| 2 | venv | `.venv` 또는 legacy conda env |
| 3 | core packages | fastapi, uvicorn |
| 4 | voice packages | edge-tts, sounddevice, 선택한 STT 모델 준비 여부 |
| 5 | tmux | 설치 여부 및 버전 |
| 6 | cloudflared | 원격 접속 도구 |
| 7 | ffmpeg | 모바일 음성 디코딩 |
| 8 | port | VT_PORT 사용 상태 |
| 9 | fsh CLI | `~/.local/bin/fsh` 심링크 (`vt`도 하위 호환 심링크로 동작) |
| 10 | PATH | `~/.local/bin` 포함 여부 |
| 11 | `.vt.env` | 설정 파일 존재 여부 |
| 12 | 인증 | 비밀번호/토큰 설정 여부 |
| 13 | 터미널 앱 | 감지된 앱 목록 + 현재 `TERM_PROGRAM` |
| 14 | Tailscale | 설치/연결 상태 |
| 15 | unix socket | `~/.vt/run/fsh.sock` 상태(켜짐 / 건너뜀 / 자가 시험 실패), 권한, 최근 24시간 기계 토큰 요청 수. `VT_AUTHZ_SOCKET=0`이면 클라이언트가 소켓을 건너뛰고 토큰만 씁니다. `fsh status`에도 한 줄로 나옵니다 |

---

## Tailscale + SSH 원격 접속

회사망이 화면 공유(크롬 원격 데스크톱·TeamViewer·RDP/VNC)를 막아둔 경우가 있습니다.
Tailscale(WireGuard 기반 VPN 메시)은 UDP 홀펀칭 또는 443 DERP 릴레이 폴백으로
동작해 이런 방화벽도 대부분 통과합니다. 화면 전체가 아니라 **터미널만** 필요하다면
Tailscale + SSH로 집 맥북의 tmux 세션에 직접 붙는 쪽이 화면 원격보다 가볍고 빠릅니다.

tmux 세션이 단일 진실의 원천이므로, SSH도 데스크톱 터미널·모바일 PWA·Voice Daemon과
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
| `fsh start --network tailscale` | Tailscale + 브라우저 | 폰에서 음성 입력, 터치 조작 |
| `fsh start` (기본, `--network all`) | 인증(비밀번호/토큰) | Tailscale 없는 완전 외부 기기 |

자세한 내용: `fsh help ssh`.

---

## 메인 터미널과 자동 오픈

`fsh terminal`로 창을 여는 앱을 확인/지정합니다(`~/.vt.env`의 `VT_TERMINAL`):

```bash
fsh terminal                  # 현재 설정 + 설치된 앱
fsh terminal set ghostty      # auto | ghostty | iterm | terminal | wezterm | warp | kitty | alacritty
fsh terminal unset            # 자동 선택으로 되돌리기
```

- `auto`(기본): 자동 선택 순서에서 설치된 첫 앱
- 설치되지 않은 앱은 `set`에서 거부되고, 실행 중 지정한 앱을 못 열면 알린 뒤 자동 선택으로 넘어갑니다
- Ghostty(1.3+)는 이미 켜진 앱에 AppleScript로 창만 추가하고, 실패하면 `open -na`로 폴백합니다
- 지원 앱: iTerm2, Ghostty, WezTerm, Kitty, Alacritty, Warp, Terminal.app (macOS), Linux는 흔한 에뮬레이터(자동 선택만)
- 이미 tmux 안에 있으면 새 창 없이 현재 창에서 계속 (`$TMUX` 체크, 멱등성 보장)

## 설치 후 통합 (선택)

새 터미널 창을 열면 자동으로 `tmux -L fsh new -A -s dev` 진입하도록 통합. 둘 중 하나를 선택.

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
