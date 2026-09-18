# FarShell

[![CI](https://github.com/Brit-juho/farshell/actions/workflows/ci.yml/badge.svg)](https://github.com/Brit-juho/farshell/actions/workflows/ci.yml)

[![English](https://img.shields.io/badge/lang-English-lightgrey.svg)](./README.md)
[![Version](https://img.shields.io/badge/version-2.1.6-blue.svg)](./CHANGELOG.md)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-informational.svg)](./docs/guide/platforms.ko.md)
[![Self-hosted](https://img.shields.io/badge/self--hosted-yes-success.svg)](#설치)

> 내 터미널을 어디서든. 한 줄로 설치.

FarShell은 macOS/Linux 머신을 개인 개발 서버로 씁니다 — 같은 tmux 세션을 음성으로도,
폰으로도 그대로 씁니다. Claude Code든 Codex든 Aider든 Gemini CLI든, 아니면 그냥 셸이든
음성 입력·모바일 접속·tmux 공유는 똑같이 동작합니다. (Windows는 WSL2에서만)

- **폰에서 터미널** — QR 스캔하면 바로 tmux
- **음성으로 코딩** — 다른 작업 중에도 `Ctrl+Shift+M`
- **원격에서 상태 확인** — 읽기 전용 코드 뷰어와 diff, 포트 대시보드, 에이전트 상태
- **Claude Code 연동** — 훅으로 도구 사용 상태를 실시간 반영하고, 완료 시 TTS 요약,
  스스로 다음 항목을 투입하는 프롬프트 큐
- **API 키도 구독도 없음** — 오픈소스 STT/TTS, 전부 무료

---

## 설치

```bash
# 터미널만 (~50MB)
curl -fsSL https://raw.githubusercontent.com/Brit-juho/farshell/master/install.sh | bash

# 터미널 + 음성 모드 (~1.5GB, Whisper STT + edge-tts TTS)
curl -fsSL https://raw.githubusercontent.com/Brit-juho/farshell/master/install.sh | bash -s voice
```

`install.sh`는 Python venv를 만들고, 고른 프로필의 패키지를 설치하고,
`~/.local/bin/fsh` 심링크를 걸고, `~/.vt.env`를 생성하고, Claude Code 훅을 등록합니다.

<details>
<summary>클론해서 설치 / 릴리스 tarball로 설치 (Node.js 불필요)</summary>

```bash
git clone https://github.com/Brit-juho/farshell.git ~/farshell
cd ~/farshell && ./install.sh          # 또는: ./install.sh voice
```

소스에서 설치하면 Vite로 프런트엔드를 빌드하느라 Node가 필요합니다.
[Releases](https://github.com/Brit-juho/farshell/releases)의 tarball에는 그 빌드가
이미 들어 있어서, Node는 개발자에게만 필요한 도구가 됩니다.

```bash
tar xzf farshell-<버전>.tar.gz && cd farshell-<버전>
./install.sh
```
</details>

---

## 빠른 시작

**다른 작업 중에 음성으로 코딩** (macOS)

```
fsh voice                 # 음성 데몬이 도는 터미널이 열린다
                          # 거기서 세션을 고른다 (예: claude --resume)
Ctrl+Shift+M              # 말하면 tmux에 바로 입력된다
                          # 결과는 이어폰에 TTS로 돌아온다
fsh stop                  # 끝나면 종료
```

**폰에서 터미널 조작**

```
fsh password              # 최초 1회 — 인증이 없으면 fsh mobile이 거부한다
fsh mobile                # URL + QR 출력, 스캔하면 끝
```

설치나 환경이 이상하면 `fsh doctor`가 진단합니다.

---

## 보안

기본값은 **무인증**입니다. 원격에 노출하기 전에 `fsh password`로 비밀번호를 설정하세요.
설정하지 않으면 `fsh mobile`이 공개 터널 열기를 거부합니다.

| 계층 | 방식 |
|---|---|
| 로그인 | 비밀번호(scrypt 해시) 또는 기계용 토큰(`VT_AUTH_TOKEN`), HMAC 서명 세션 쿠키(24h) |
| 새 기기 | 90일 쿠키로 기기별 신뢰. `fsh otp setup`을 켜면 처음 보는 기기에만 6자리 코드 요구 |
| 폐기 | `fsh device revoke <id>` — 해당 기기의 세션까지 즉시 무효화 |
| 크로스 사이트 | Origin이 자기 자신이 아니면 HTTP·WS 모두 403. CORS 와일드카드 없음 |
| 코드 뷰어 | 읽기 전용, 지정한 루트 안으로 제한, 거부 목록(`.env*`·`*.pem`·`.ssh/` 등)이 파일 열람과 `git diff`에 동일 적용 |
| E2E 암호화 | `--e2e` — X25519 키교환 + NaCl SecretBox, 장기 Ed25519 신원키로 세션 키를 서명해 TOFU 방식으로 능동적 중간자 공격 방어 |

상세는 [`docs/ref/auth.md`](./docs/ref/auth.md).

---

## 문서

| 무엇 | 어디 |
|---|---|
| `fsh` 명령어와 옵션 전체 | [`CLI.ko.md`](./CLI.ko.md) |
| REST / WebSocket 엔드포인트 | [`API.md`](./API.md) (영문 단일본) |
| 모듈 지도, 3-plane 모델, 데이터 흐름 | [`ARCHITECTURE.md`](./ARCHITECTURE.md) (영문 단일본) |
| 디자인 시스템 — 원칙·스킨·토큰·레이아웃 | [`DESIGN.md`](./DESIGN.md) (영문 단일본) |
| 릴리스 이력 | [`CHANGELOG.md`](./CHANGELOG.md) |
| 지원 플랫폼, WSL2, 음성 엔진 | [`docs/guide/platforms.ko.md`](./docs/guide/platforms.ko.md) |
| 설정 키 | [`config/vt.defaults.env`](./config/vt.defaults.env) — 커밋된 기본값, 키마다 설명 주석 |
| 문제 해결 | [`docs/help/troubleshoot.md`](./docs/help/troubleshoot.md) (`fsh help troubleshoot`와 같은 내용) |

**영역별 기능 레퍼런스** — 무엇을 하는지만이 아니라 왜 그렇게 설계했고 어떤 사고가
있었는지가 함께 적혀 있습니다 (영문 단일본).

| 영역 | 문서 |
|---|---|
| 인증, 기기, 경계값 | [`docs/ref/auth.md`](./docs/ref/auth.md) |
| 터미널, 페인, 붙여넣기, 스크롤백, 테마 | [`docs/ref/terminal.md`](./docs/ref/terminal.md) |
| 에이전트 상태, 프롬프트 큐, 스니펫, 사용량, MCP | [`docs/ref/agents.md`](./docs/ref/agents.md) |
| 파일, 공유 링크, 코드 뷰어 | [`docs/ref/files.md`](./docs/ref/files.md) |
| 터널, 포트, Tailscale, 원격 접속 | [`docs/ref/tunnel.md`](./docs/ref/tunnel.md) |
| 워크트리와 git | [`docs/ref/worktree.md`](./docs/ref/worktree.md) |
| 멀티호스트 페어링 | [`docs/ref/multihost.md`](./docs/ref/multihost.md) |
| 음성, 클립보드, Web Push | [`docs/ref/voice.md`](./docs/ref/voice.md) |
| 실행·테스트·수동 설치 | [`docs/ref/dev.md`](./docs/ref/dev.md) |

작업하는 AI 에이전트(Claude Code, Codex 등)는 [`AGENTS.md`](./AGENTS.md)에서 시작합니다.

---

## 라이선스

MIT
