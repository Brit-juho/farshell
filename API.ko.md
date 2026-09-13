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
| GET | `/api/sessions/{id}/scrollback[?before=&limit=]` | N13 — 영속 스크롤백 로그에서 "더 불러오기"(`scrollback.persist` 켰을 때만 데이터 있음). `data_b64` 인코딩, `next_before`로 최신→과거 페이지네이션 |
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
| GET | `/api/search/scrollback?q=X[&sessions=all\|id1,id2]` | scrollback을 grep한다(N40 — 팔레트 `~` 모드). **세션 하나당 소스 하나**: 영속 로그가 있으면 로그, 없으면 링버퍼(링버퍼는 로그의 꼬리라 둘 다 보면 모든 줄이 두 번 나온다) — 결과의 `source: "log"|"live"`가 어느 쪽인지 밝힌다. 로그는 세션보다 오래 남으므로 **이미 끝난 세션·서버 재시작 이전의 출력**도 찾힌다. `sessions=all`(기본)이면 열린 세션 전부 + 로그가 남은 세션 전부, 콤마 목록이면 그 id들만. 세션당 20건·전체 50건, 앞뒤 3줄 컨텍스트. 로그는 꼬리에서 4MB까지만 훑고 잘리면 `truncated`를 세운다 |

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
| GET | `/api/tunnel/list` | N22 — 지금 열려 있는 모든 터널(메인 + `fsh tunnel expose`한 포트) 요약, 포트 탭 "노출 중" 섹션용. 해제는 위 `DELETE /api/ports/{port}/expose`를 그대로 재사용 |

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
| GET | `/api/files?filter=all\|shared\|expiring` | 저장된 파일 목록 → `{items, quota:{used,max,ttl_days}}` (quota는 필터와 무관하게 항상 저장소 전체) |
| GET | `/api/files/{id}/download` | id로 다운로드 (`attachment` + `nosniff` + `no-store`) |
| GET | `/api/files/{id}/path` | 저장된 파일의 디스크 경로 (dock 파일 탭 「경로 복사」용 — 입력은 id뿐이라 traversal 표면이 없다) |
| DELETE | `/api/files/{id}` | 저장된 파일 삭제 |
| POST | `/api/files/{id}/insert` | 파일 경로를 tmux 세션 pane에 타이핑(JSON: `session`), Enter는 안 누름 |
| POST | `/api/files/{id}/send` | 파일을 원격 호스트로 복사(JSON: `host`, 선택 `session`). `session`을 주면 상대가 **자기 쪽 경로**를 그 pane에 타이핑한다. 이 라우트는 평소 로그인 인증이고, 나가는 요청에만 peer 서명이 붙는다. 같은 파일 재전송은 origin으로 건너뛴다 |
| POST | `/api/files/{id}/share` | **승격 필요.** 공유 링크 발급(JSON: `mode:"device"\|"pin"`, `ttl`, `once`, `pin?`) → `{share, token, url}` |
| DELETE | `/api/files/{id}/share/{shareId}` | **승격 필요.** 공유 취소 — 서명이 아직 유효해도 그 즉시 URL이 404가 된다 |

공개 다운로드 진입점(api 접두사 밖이고, 평소의 세션/토큰 인증도 미적용 —
`server/routes/share.py`가 토큰·모드별 검증을 직접 한다):

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/s/{token}` | 공유 다운로드. `device` 모드는 `vt_device`+`vt_session`이 유효하면 바로 다운로드, 아니면 `/?next=/s/{token}`으로 302. `pin` 모드는 1회용 다운로드 쿠키가 없으면 PIN 입력 페이지 |
| POST | `/s/{token}/pin` | PIN 검증(form: `pin`) → 60초 1회용 다운로드 쿠키 + 리다이렉트. 5회 실패 시 공유 취소 |

## 멀티호스트 페어링 (N7/N39 1단계)

다른 맥의 FarShell 서버가 호출하는 엔드포인트. 의도적으로 **별도 네임스페이스**다 —
peer 자격증명은 여기 정의된 것에만 닿고, 나머지 API에는 절대 못 넘어간다.
`TokenAuthMiddleware`는 /api/peer 접두사를 우회하고(peer에겐 브라우저 세션 쿠키가 없다),
`server/routes/peer.py`가 HMAC 서명을 직접 검증한다.

`pair`를 제외한 모든 호출의 인증 헤더: `X-Peer-Id`, `X-Peer-Ts`, `X-Peer-Nonce`,
`X-Peer-Sig` = `HMAC(secret, "METHOD\npath\nts\nnonce")`. **secret은 전송되지 않는다.**
60초 시간창 + 1회용 nonce(재생 차단), 서명이 method+path에 묶여 있어 `view`용 GET
서명을 `control`용 POST에 돌려쓸 수 없다.

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/api/peer/pair` | 1회용 페어링 티켓 제출(JSON: ticket, id, label, version) → 그 연결 전용 secret 발급. 서명 없이 열리는 유일한 peer 경로 — 아직 공유 secret이 없는 시점이라 티켓이 그 역할을 한다. 예약 id(`local`/`self`/`me`)는 **티켓을 소모하지 않고** 거부 |
| GET | `/api/peer/ping` | 서명된 연결 확인 → id·label·version·`serverTime`(호출자가 이걸로 시계 오차를 계산)·호출자에게 부여된 등급 |
| GET | `/api/peer/sessions` | 서명 필요, `view` 등급 — 이 호스트의 tmux 세션 + 에이전트 상태. **다른 peer의 세션은 절대 중계하지 않는다**(hop 0): A↔B 상호 페어링에서 A→B→A 무한 재귀가 되기 때문 |
| POST | `/api/peer/input` | 서명 + **`control` 등급** — 이 호스트의 tmux 세션에 텍스트를 타이핑(JSON: `session`, `data`, 선택 `enter`). 기본은 Enter 없음(파일 삽입과 같은 계약), `enter: true`면 Enter까지 — 프롬프트 큐(A3)가 그것을 쓴다. 어느 쪽인지 호출부가 명시하고 서버는 추측하지 않는다. `view` 상대에겐 켜는 명령까지 담아 403 |
| WS | `/api/peer/ws/{tmux_name}` | 서명 필요. `view`는 출력 구독, 입력은 `control`에서만 적용. **상대 서버의 프록시**가 여는 소켓이다(브라우저 WebSocket은 서명 헤더를 못 보낸다). 연결마다 이 호스트에 전용 PTY를 만들고 끊길 때 정리한다 — 소유자의 PTY를 공유하면 두 화면이 크기를 두고 싸운다 |
| POST | `/api/peer/file` | **본문 해시까지 서명**(`X-Peer-Body`) + **`control` 등급** — 원시 바이트를 받아 이 호스트의 파일 저장소에 넣는다. `X-Peer-File-Session`이 오면 그 pane에 경로를 타이핑(Enter 없음). 같은 파일 재전송은 origin(보낸 호스트 id + 그쪽 파일 id)으로 건너뛴다 — 내용 해시가 아니다. 이 호스트의 `VT_MAX_UPLOAD_MB`를 넘으면 413 |

## 호스트 (N7/N39 2단계 — 브라우저가 부르는 쪽)

peer 네임스페이스의 나가는 짝을 로컬 UI에게 하나의 목록으로 합쳐 준다. 평소의
인증이 지키는 일반 API다(로그인한 사람만) — peer 서명으로만 열리는 peer 네임스페이스와 반대.

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/hosts[?fresh=1]` | 로컬(항상 첫 항목, id `local`) + 등록된 모든 원격 호스트를 각자의 세션 목록과 함께. 원격 조회는 병렬 + 30초 캐시이며, 꺼진 호스트는 목록을 실패시키지 않고 `online:false` + `reason`으로 표현된다 |
| GET | `/api/hosts/self` | 이 호스트의 id/label (페어링 안내·설정 화면용) |
| POST | `/api/hosts/{id}/ping` | 연결 확인 + 시계 오차/지연 갱신. 연결 실패는 **200에 `online:false`** — 서버 오류가 아니라 상태이기 때문 |
| WS | `/ws/remote/{host_id}/{tmux_name}` | 원격 pane 프록시: 이쪽은 브라우저의 평소 로그인 인증, 저쪽은 peer 서명. **PTY를 만들지 않고, 출력 감시에 먹이지 않고, 스크롤백도 안 쓴다** — 알림·영속화는 PTY를 소유한 호스트의 몫이라 여기서 겹치지 않는다 |

## 기타

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/capabilities` | 서버 capability 정보 (TTS/STT/터널/버전 등) |
| GET | `/api/scrollback/usage` | N13 — 영속화 켜짐 여부 + `~/.vt/scrollback/` 전체 디스크 사용량(설정 화면용) |
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
