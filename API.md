# API 레퍼런스

FarShell 서버(`server/main.py`)가 제공하는 REST/WebSocket 엔드포인트 전체
목록입니다. 개요는 [README.md](./README.md), 구조는 [ARCHITECTURE.md](./ARCHITECTURE.md)를
참고하세요.

**인증:** 비밀번호(`fsh password`) 또는 토큰(`VT_AUTH_TOKEN`)이 설정돼 있으면 모든
엔드포인트에 인증이 필요합니다. 사람은 로그인 후 발급되는 `vt_session` 쿠키로,
데몬/스크립트는 `?token=xxx` 쿼리 또는 `Authorization: Bearer xxx` 헤더로 인증합니다.
자세한 인증 모델은 [README.md의 보안 섹션](./README.md#보안)을 참고하세요.

---

## 세션 / PTY

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/sessions` | 세션 목록 |
| POST | `/api/sessions` | 세션 생성 (JSON: cols, rows, name) |
| DELETE | `/api/sessions/{id}` | 세션 삭제 |
| PATCH | `/api/sessions/{id}` | 세션 이름 변경 (JSON: name) — tmux 세션명도 함께 변경(영숫자/dash/underscore만) |
| POST | `/api/watch/{id}` | 출력 감시 ON/OFF (JSON: enabled, timeout) |

## tmux

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/tmux/sessions` | tmux 세션 목록 |
| POST | `/api/tmux/attach` | tmux 세션에 attach (JSON: name) |
| POST | `/api/tmux/create` | tmux 세션 생성 + 자동 attach (JSON: name, cols, rows) |
| DELETE | `/api/tmux/kill/{name}` | tmux 세션 완전 종료 |
| POST | `/api/tmux/open-on-mac` | 이미 존재하는 tmux 세션을 서버(macOS) 터미널에 새 창으로 attach (JSON: name). 서버가 macOS가 아니면 400 |
| GET | `/api/tmux/preview/{name}?lines=20&ansi=1` | Grid 뷰용 tmux pane 최근 출력 캡처 |

## 음성

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/voice/input?session_id=X` | 음성 → STT → 세션 입력 |
| POST | `/voice/output` | 텍스트 → TTS → 오디오 반환 |
| POST | `/voice/cancel` | 재생 중인 TTS 즉시 중단 (barge-in) |
| POST | `/voice/local/start` | MacBook 마이크 녹음 시작 |
| POST | `/voice/local/stop?session_id=X` | 녹음 종료 → STT → 세션 입력 |
| GET | `/voice/stt/status` | STT 모델 준비 상태 조회 (모델을 로드하지 않음) |
| POST | `/voice/stt/preload` | STT 모델 미리 로드 — 음성 모드 켤 때 첫 입력 지연 제거 |
| POST | `/voice/stt/unload` | STT 모델 언로드 — 음성 모드 끌 때 메모리(~150MB) 회수 |

## 인증

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/api/auth` | 로그인 — 비밀번호(+새 기기면 `otp`) 또는 1회용 `ticket` → `vt_session`/`vt_device` HttpOnly 쿠키 발급. 401 `otp_required`/`otp_invalid`, 429 `otp_locked` |
| GET | `/api/auth/status` | 인증 활성 여부 / OTP 연동 여부 / 이 기기 등록 여부 (미인증 접근 가능, 비밀 미포함) |
| POST | `/api/auth/logout` | 세션만 해제 (기기 등록은 유지) |

## 코드 뷰어 / diff (읽기 전용)

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/fs/roots` | 열람 가능한 루트 목록 (기본 `~/GitHub`) |
| GET | `/api/fs/tree?path=X` | 디렉토리 목록. `.git`/`node_modules` 등 제외 |
| GET | `/api/fs/file?path=X` | 파일 내용. 바이너리는 `binary:true`만, 512KB 초과는 절단 |
| GET | `/api/git/status?repo=X` | `git status --porcelain` 파싱 결과 |
| GET | `/api/git/diff?repo=X[&file=Y][&staged=1]` | `git diff` 원문. `.env`/`*.pem`/`id_rsa` 등 보호 경로는 내용이 가려짐(`[내용 가려짐 — 보호된 경로]`) |

거부 목록(`.env*`, `*.pem`, `id_rsa`, `.ssh/`, `.aws/` 등)에 걸리는 경로는 `/api/fs/file`뿐
아니라 `/api/git/diff`에서도 동일하게 가려집니다 — 판정은 `server/fsguard.py` 한 곳에만 있습니다.

읽기 전용이 아닌 Git 액션(코드 뷰어에서 stage/commit용):

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/api/git/stage` | 파일 스테이지 (JSON: repo, files). 응답은 갱신된 status |
| POST | `/api/git/unstage` | 스테이지 해제 — 워킹트리는 안 건드리고 인덱스만 되돌림 (JSON: repo, files) |
| POST | `/api/git/commit` | 스테이지된 변경사항 커밋 (JSON: repo, message). 스테이지된 게 없으면 400 |

## 프롬프트 큐

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/queue` | 큐 목록 |
| POST | `/api/queue` | 큐에 추가 (JSON: text, target). 상한 50, 초과 시 409 |
| DELETE | `/api/queue/{id}` | 항목 삭제. `id=all`이면 전체 비우기 |
| POST | `/api/queue/{id}/unblock` | safe_mode에 막힌 항목 재개 |
| POST | `/api/queue/run` | 수동 드레인 — 한 건 투입 |

## 포트 대시보드

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/ports[?fresh=1]` | 리스닝 포트 목록 (3초 캐시) |
| DELETE | `/api/ports/{port}[?pid=N]` | 프로세스 종료. `pid` 불일치 시 409 (VT 서버 자신/cloudflared/tailscaled/sshd는 종료 불가) |
| POST | `/api/ports/{port}/expose` | Cloudflare 터널로 공개. 본문 `{"confirm":true}` 필수(없으면 428) |
| DELETE | `/api/ports/{port}/expose` | 해당 포트 터널 종료 |

## Web Push

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/push/key` | VAPID 공개키 (브라우저 구독용) |
| POST | `/api/push/subscribe` | 구독 등록 (JSON: subscription, label) |
| DELETE | `/api/push/subscribe` | 구독 해제 (JSON: endpoint) |
| POST | `/api/push/test` | 테스트 알림 발송 |
| GET | `/api/push/status` | 구독 수 / 현재 origin / origin 어긋난 구독 수 |

## 기타

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/api/upload?session_id=X` | 파일 업로드 (multipart/form-data) |
| GET | `/api/download?path=X` | 서버 파일 다운로드 |
| GET | `/api/capabilities` | 서버 capability 정보 (TTS/STT/터널 등) |
| GET | `/api/workspace` | 워크스페이스 동기화 조회 (탭/UI 상태) |
| PUT | `/api/workspace` | 워크스페이스 상태 저장 |
| GET | `/api/agents` | tmux 세션별 활성 에이전트 (claude 등) 전체 목록 |
| GET | `/api/agents/{name}` | 특정 tmux 세션의 활성 에이전트 정보 |
| GET | `/api/agent/status` | Claude Code 훅으로 추적 중인 도구 사용 상태 (그리드 뷰 펄스/완료 배지용) |
| POST | `/api/agent/event` | Claude Code Pre/Post/StopToolUse 훅이 호출하는 엔드포인트 |
| GET | `/api/safe-mode` | 프롬프트 큐 safe_mode 활성 여부 |
| GET | `/api/tailscale/status` | Tailscale 설치/연결/IP/MagicDNS 호스트명 |
| GET | `/api/tunnel/status` | Cloudflare 터널(메인) 연결 상태 |
| GET | `/api/notify/status` | ntfy/Telegram 알림 설정 여부 |
| POST | `/api/notify/test` | 테스트 알림 발송 (JSON: title, message, priority) |
| POST | `/api/notify/client-event` | tmux client-attached/detached 훅 전용 — SSH 접속 가시화 |
| POST | `/api/clipboard/push` | `clipboard_daemon.py` 전용 — `/ws-notify` 클라이언트에 브로드캐스트 |

## WebSocket

| 경로 | 설명 |
|------|------|
| `/ws/{id}` | 터미널 WebSocket (xterm.js 연결). `?e2e=1`로 E2E 암호화 |
| `/ws-notify` | 작업 완료 알림 수신 |
| `/ws-preview/{name}` | Grid 뷰용 tmux pane 출력 push |
| `/ws-agent` | 에이전트 활성 상태 push |
| `/ws-workspace` | 워크스페이스 변경 push |
