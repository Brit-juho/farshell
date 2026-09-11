# API 레퍼런스

FarShell 서버(`server/main.py`)가 제공하는 REST/WebSocket 엔드포인트 전체
목록입니다. 개요는 [README.md](./README.md), 구조는 [ARCHITECTURE.md](./ARCHITECTURE.md)를
참고하세요.

**인증:** 비밀번호(`fsh password`) 또는 토큰(`VT_AUTH_TOKEN`)이 설정돼 있으면 모든
엔드포인트에 인증이 필요합니다. 사람은 로그인 후 발급되는 `vt_session` 쿠키로,
데몬/스크립트는 `?token=xxx` 쿼리 또는 `Authorization: Bearer xxx` 헤더로 인증합니다.
자세한 인증 모델은 [README.md의 보안 섹션](./README.ko.md#보안)을 참고하세요.

---

## 세션 / PTY

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/sessions` | 세션 목록 |
| POST | `/api/sessions` | 세션 생성 (JSON: cols, rows, name) |
| DELETE | `/api/sessions/{id}` | 세션 삭제 |
| PATCH | `/api/sessions/{id}` | 세션 이름 변경 (JSON: name) — tmux 세션명도 함께 변경(영숫자/dash/underscore만) |
| POST | `/api/sessions/{id}/keys` | PTY에 텍스트를 직접 써 넣는다 (JSON: text) — 터미널 WS 타이핑과 동급 권한. 존재하지 않는 세션은 404. 모바일 플릿 홈의 인라인 승인 버튼이 쓴다(N38) |
| POST | `/api/watch/{id}` | 출력 감시 ON/OFF (JSON: enabled, timeout) |

## tmux

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/tmux/sessions` | tmux 세션 목록 |
| POST | `/api/tmux/attach` | tmux 세션에 attach (JSON: name) |
| POST | `/api/tmux/create` | tmux 세션 생성 + 자동 attach (JSON: name, cols, rows, cwd) |
| DELETE | `/api/tmux/kill/{name}` | tmux 세션 완전 종료 |
| POST | `/api/tmux/open-on-mac` | 이미 존재하는 tmux 세션을 서버(macOS) 터미널에 새 창으로 attach (JSON: name). 서버가 macOS가 아니면 400 |
| GET | `/api/tmux/preview/{name}?lines=20&ansi=1` | Grid 뷰용 tmux pane 최근 출력 캡처 |
| GET | `/api/tmux/clients?session=X&me=Y` | 세션에 붙은 클라이언트 목록(C1). `me`는 요청자의 web session id — 서버가 그걸로 tty를 역산해 `is_me`를 표시한다 |
| POST | `/api/tmux/detach-client` | 클라이언트 하나 끊기(C1, JSON: tty, me). 자기 자신을 끊으면 400 — 복구 경로가 없다 |
| POST | `/api/tmux/clients/solo` | "이 화면만 남기기"(C2, JSON: session, me). 남길 tty를 클라이언트가 보내지 않는다 — 서버가 역산하고, 못 하면 전부 끊는 대신 400 |

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
| POST | `/api/auth/elevate` | 비밀번호(+OTP 연동 시 OTP) 재확인 → `vt_session`에 15분짜리 `elev` 클레임을 얹어 재발급(N31). 기기 스코프가 아니라 세션 스코프. 401 `invalid`/`otp_required`/`otp_invalid`, 429 `*_locked` |
| GET | `/api/auth/elevation` | 현재 세션의 승격 상태(`elevated`·`elevated_until`) + `unused: true`·`unused_reason: "ADR-27"` — 지금은 승격을 요구하는 경로가 없다. 읽기 전용(설정 → 보안) |
| GET | `/api/devices` | 등록 기기 목록(`~/.vt/devices.json`): id(앞 8자만)·라벨·등록·마지막 사용·`current`(요청한 기기). 읽기 전용 — 등록/폐기는 `fsh device` |

## 코드 뷰어 / diff (읽기 전용)

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/fs/roots` | 열람 가능한 루트 목록 (기본 `~/GitHub`) |
| GET | `/api/fs/tree?path=X` | 디렉토리 목록. `.git`/`node_modules` 등 제외 |
| GET | `/api/fs/search?q=X` | 파일명 fuzzy 검색 (N5/N40 — 커맨드 팔레트 `/` 모드). `path=`로 하위 트리 한정, 최대 50건 |
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
| GET | `/api/git/log?repo=X[&file=Y]` | 최근 커밋 목록 |
| GET | `/api/git/show?repo=X&rev=Y` | 커밋 하나의 diff |

## git 계정 · 바인딩 (N30, GET 제외 승격 세션 필요)

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/git/accounts` | 계정 목록 — 토큰 원문 없이 `auth.masked`(`ghp_…3f2a`)만 |
| POST | `/api/git/accounts` | 승격 필요(`require_elevated`). 저장 전 GitHub/GitLab `GET /user`로 PAT 검증 후 `login` 자동 채움 |
| DELETE | `/api/git/accounts/{account_id}` | 승격 필요. 이 계정을 가리키는 바인딩도 함께 제거 |
| GET | `/api/git/binding?repo=X` | 저장소의 해석된 계정 id: `byRepo` → 원격 URL의 `host/owner`가 `byHostOrg` → 같은 host에 계정이 정확히 1개 → `null` |
| PUT | `/api/git/binding` | 승격 필요. body `{repo, account_id}` — `byRepo` 명시 바인딩 설정 |

## 스크롤백 검색

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/search/scrollback?q=X[&sessions=all\|id1,id2]` | 각 세션의 살아있는 scrollback 링버퍼를 grep한다(N40 — 팔레트 `~` 모드, WS 재접속이 쓰는 것과 같은 버퍼, 별도 저장소 없음). `sessions=all`(기본)이면 열린 세션 전부, 콤마 목록이면 그 세션들만. 세션당 20건·전체 50건까지 반환하며, 각 결과는 앞뒤 3줄 컨텍스트를 포함한다 |

## 프롬프트 큐

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/queue` | 큐 목록 |
| POST | `/api/queue` | 큐에 추가 (JSON: text, target). 상한 50, 초과 시 409 |
| DELETE | `/api/queue/{id}` | 항목 삭제. `id=all`이면 전체 비우기 |
| POST | `/api/queue/{id}/unblock` | safe_mode에 막힌 항목 재개 |
| POST | `/api/queue/run` | 수동 드레인 — 한 건 투입 |

## 워크트리 (N8/N44)

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/worktrees` | `VT_BROWSE_ROOTS`(+ `~/.worktrees`) 아래 모든 저장소의 git 워크트리 합산 — 세션 매핑 + diff 요약. 5초 캐시 |
| GET | `/api/worktrees/precheck?repo&base` | 만들기 다이얼로그 전에 lockfile 배너 판정(`warnings: ["lockfile_mismatch"]`) |
| POST | `/api/worktrees` | 워크트리 생성(`git worktree add` + node_modules/`.env`/포트 대역/에이전트). 실패 시 롤백 |
| DELETE | `/api/worktrees/{id}` | 워크트리 삭제. 변경 있으면 409 + `dirty:true`(`force:true`로 재요청), `killSessions:true`면 tmux 세션도 kill |
| POST | `/api/worktrees/{id}/open` | 기존 tmux 세션에 붙거나, 없으면 `wt-<repoName>-<branch>` 생성 |

## 포트 대시보드

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/ports[?fresh=1]` | 리스닝 포트 목록 (3초 캐시) |
| DELETE | `/api/ports/{port}[?pid=N]` | 프로세스 종료. `pid` 불일치 시 409 (VT 서버 자신/cloudflared/tailscaled/sshd는 종료 불가) |
| POST | `/api/ports/{port}/expose` | Cloudflare 터널로 공개. 본문 `{"confirm":true}` 필수(없으면 428) |
| DELETE | `/api/ports/{port}/expose` | 해당 포트 터널 종료 |

## 프롬프트 스니펫

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/snippets` | 저장된 프롬프트 스니펫 목록 |
| GET | `/api/snippets/project?cwd=` | cwd → 스니펫 project 키(저장소 top, 아니면 null) |
| POST | `/api/snippets` | 스니펫 추가 (JSON: text, label, scope: global\|project, cwd) |
| DELETE | `/api/snippets/{id}` | 스니펫 삭제 |

## Web Push

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/push/key` | VAPID 공개키 (브라우저 구독용) |
| POST | `/api/push/subscribe` | 구독 등록 (JSON: subscription, label) |
| DELETE | `/api/push/subscribe` | 구독 해제 (JSON: endpoint) |
| POST | `/api/push/test` | 테스트 알림 발송 |
| GET | `/api/push/status` | 구독 수 / 현재 origin / origin 어긋난 구독 수 |

## 파일 (N19)

`/tmp/vt-uploads`(경로 기반)를 `~/.vt/files/`(id 기반)로 대체했다. 기존 업로드는
서버 기동 시 자동 이전된다. 경로 기반 다운로드 API는 없다 —
`/api/files/{id}/download`는 설계상 id로만 접근한다.

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/api/upload?session_id=X` | 파일 업로드 (multipart/form-data) → file_store에 저장, 응답에 `id` 포함 |
| GET | `/api/files?filter=all\|shared\|expiring` | 저장된 파일 목록 |
| GET | `/api/files/{id}/download` | id로 다운로드 (`attachment` + `nosniff` + `no-store`) |
| DELETE | `/api/files/{id}` | 저장된 파일 삭제 |
| POST | `/api/files/{id}/insert` | 파일 경로를 tmux 세션 pane에 타이핑(JSON: `session`), Enter는 안 누름 |

## 기타

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/capabilities` | 서버 capability 정보 (TTS/STT/터널/버전 등) |
| GET | `/api/workspace` | 워크스페이스 동기화 조회 (탭/UI 상태) |
| PUT | `/api/workspace` | 워크스페이스 상태 저장 |
| GET | `/api/device-settings` | 이 기기의 설정 조회 (N3 — `vt_device` 쿠키로 기기 식별, 없으면 `local`) |
| PUT | `/api/device-settings` | 이 기기의 설정 저장 |
| GET | `/api/agents` | tmux 세션별 활성 에이전트 (claude 등) 전체 목록 |
| GET | `/api/agents/{name}` | 특정 tmux 세션의 활성 에이전트 정보 |
| GET | `/api/agents/coverage` | N9/N45 — CLI별 승인 대기 감지 커버리지: `[{cli, path:"hook"\|"pty"\|"none", patternLines, states, trust:"high"\|"mid"\|"low"}]`, `detect/*.toml`을 실시간으로 읽는다 |
| GET | `/api/agent/status` | 에이전트 상태 머신(A1) — 세션별 `idle/working/waiting/done` + TTL 만료 |
| POST | `/api/agent/report` | pane 자기보고(A2) — 훅이 없는 에이전트용 (`fsh pane report`) |
| GET | `/api/hooks/status` | Claude Code 훅 등록 상태(A0/S4) — `{ok, events:{PreToolUse,PostToolUse,Stop}}` |
| GET | `/api/usage` | 사용량 스냅샷(U1) — 소스가 없으면 `{available:false, reason}`. 토큰·credential은 화이트리스트로 제외 |
| GET | `/api/usage/counter` | N41 — 누적형(한도 없음, 로컬 LLM 등) CounterProvider 스냅샷. `?since=<epoch>`로 누적 집계 창 선택, 7일 스파크라인은 무관하게 고정 |
| POST | `/api/usage/counter` | N41 — 사용량 이벤트 기록(`{model, tokens, seconds}`) — `fsh usage add`와 같은 저장소 |
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
