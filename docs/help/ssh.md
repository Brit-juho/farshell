# vt help ssh — Tailscale + SSH 원격 접속

회사망처럼 화면 원격(크롬 원격 데스크톱, TeamViewer, RDP/VNC 등)이 막혀 있는
환경에서도 대부분 **Tailscale**은 통과합니다 (UDP 홀펀칭 또는 443 DERP
릴레이 폴백 — 겉보기엔 평범한 아웃바운드 트래픽). 화면 전체가 아니라
**터미널만** 필요하다면 Tailscale + SSH로 이 맥북의 tmux 세션에 직접
붙는 쪽이 더 가볍고 빠릅니다.

## 왜 굳이 vt와 엮나

farshell의 핵심 설계는 "tmux 세션이 단일 진실의 원천" — 데스크톱
iTerm, 모바일 PWA, Voice Daemon이 전부 같은 `tmux -L vt` 세션에 붙는다
(`vt help concepts` 참고). **SSH도 다섯 번째 클라이언트일 뿐**이다.
회사에서 SSH로 붙어도 집에서 보던 것과 완전히 같은 화면·스크롤백·
실행 중인 Claude 세션을 그대로 이어받는다.

## 사용법

```bash
vt ssh                 # 세션 'dev'로 접속하는 명령 안내
vt ssh mysession       # 다른 세션 이름 지정
vt ssh --user alice    # 원격 로그인 계정 지정 (기본: 현재 계정)
```

실행하면 **이 머신에서** Tailscale 상태를 읽어 다른 기기(회사 노트북 등)
에서 그대로 복사해 실행할 두 가지 명령을 출력합니다:

1. 일반 SSH — `ssh -t user@100.x.x.x 'tmux -L vt attach -t dev || tmux -L vt new -A -s dev'`
   - 이 머신의 `~/.ssh/authorized_keys`에 접속할 기기의 공개키가 등록돼 있어야 함
2. Tailscale SSH — `tailscale ssh user@100.x.x.x -- '...'`
   - tailnet ACL에서 SSH가 허용돼 있으면 **키 등록 없이** Tailscale 계정 인증만으로 접속

## 공개키 등록이 안 돼 있다면

접속할 기기(회사 노트북 등)에서:
```bash
cat ~/.ssh/id_ed25519.pub    # 없으면 ssh-keygen -t ed25519로 생성
```
그 출력을 복사해 **이 머신에서**:
```bash
vt ssh --add-key "ssh-ed25519 AAAA... user@laptop"
```
`~/.ssh/authorized_keys`에 중복 없이 추가됩니다 (직접 붙여넣은 값만 반영 — 자동 실행 없음).

## vt mobile과의 관계

`vt mobile --network tailscale`로 실행하면 웹 UI(xterm.js + 음성)도
Cloudflare Tunnel 없이 tailnet IP로만 노출됩니다. 즉:

| 접속 방식 | 필요한 것 | 용도 |
|---|---|---|
| `vt ssh` | Tailscale + (선택) SSH 키 | 순수 터미널, IDE/vim 등 키 입력이 무거운 작업 |
| `vt mobile --network tailscale` | Tailscale + 브라우저 | 폰에서 음성 입력, 터치 조작 |
| `vt mobile` (기본, `--network all`) | 아무것도 (공개 URL) | 완전 외부망, Tailscale 없는 기기 |

셋 다 같은 tmux 세션을 공유하므로 아무 조합이나 섞어 써도 된다.

## 진단

```bash
vt doctor      # Tailscale 설치/실행 상태 확인 항목 포함
vt status      # 현재 Tailscale IP 표시
```
