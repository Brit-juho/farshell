# TODOS

작업 항목 목록. 완료 시 `- [x]` 표시.

---

## 아키텍처

- [x] **[D6] `server/main.py` routes/ 분리** ✅ 완료 (커밋 111d2dc, 2026-05-07)
  - **What:** main.py 936줄 / 37라우트를 `routes/pty.py`, `routes/tmux.py`, `routes/voice.py`, `routes/tunnel.py`로 분리
  - **Why:** 새 라우트 추가마다 단일 파일이 커져 코드 탐색 비용 증가
  - **Context:** 2026-05-07 plan-eng-review(D6)에서 확인. 현재는 관리 가능 수준(936줄)이나 다음 큰 기능 추가 전 진행 권장
  - **Approach:** FastAPI `APIRouter` + `app.include_router()`로 점진적 이동. 기존 엔드포인트 경로 변경 없음

## 네트워크 / 원격 접속

- [x] **[D9] Tailscale + SSH 원격 접속** ✅ 완료 (2026-07-07)
  - **What:** `vt ssh`(SSH/`tailscale ssh` attach 명령 안내 + `--add-key`), `vt mobile --network tailscale`,
    `network_access.py`의 `tailscale` 모드/키워드, `server/tailscale.py`(상태 감지),
    `VT_NOTIFY_CLIENT_EVENTS`(tmux client-attached/detached → push 알림)
  - **Why:** 회사망처럼 화면 원격(크롬 원격 데스크톱/TeamViewer/RDP/VNC)이 막힌 환경에서도
    Tailscale은 대개 통과함. 터미널만 필요하면 화면 원격보다 Tailscale+SSH로 기존 tmux 세션에
    바로 붙는 게 더 가볍고, "tmux가 단일 진실의 원천"이라는 기존 설계와도 자연스럽게 들어맞음
  - **Context:** `docs/help/ssh.md`, `CHANGELOG.md` [1.5.0], `ARCHITECTURE.md` 4.7/6/7 참고
  - **알려진 한계 (후속 개선 후보):**
    - 클라이언트 접속 알림의 원격 호스트 판별은 `who` 출력 파싱에 의존하는 best-effort —
      플랫폼별 `who` 포맷 차이(macOS/Linux) 또는 `utmp` 미기록 환경에선 호스트가 빈 문자열로 옴
      (이 경우 알림 자체는 여전히 오지만 "어디서" 정보만 빠짐)
    - `tmux run-shell` 훅은 tmux 서버 프로세스 컨텍스트에서 실행되므로 `~/.vt.env`의
      `VT_PORT`/`VT_TOKEN`을 훅 스크립트가 직접 다시 읽어야 함 (tmux `set-environment`로 서버
      기동 시점 값을 넘기는 방식이 더 견고할 수 있음 — 현재는 단순성 우선으로 스크립트가 매번
      `~/.vt.env`를 source)
    - `vt ssh`가 등록하는 SSH 공개키(`--add-key`)는 검증 없이 형식만 확인 — 실수로 잘못된 기기의
      키를 등록해도 막지 않음 (사용자 책임 하에 동작하는 저수준 유틸리티로 설계)

## 인프라

- [ ] **[D1] 멀티워커 환경에서 WS 연결 카운터 공유**
  - **What:** `_ws_total_count` / `_ws_count_per_session`을 프로세스 간 공유 (Redis 또는 `multiprocessing.Value`)
  - **Why:** `uvicorn --workers N` 사용 시 프로세스별 독립 카운터로 연결 한도가 무의미해짐
  - **Context:** 로컬 LLM 전환 시 멀티워커가 필수. 현재 단일 워커라 문제 없음. `server/main.py:530-531` 참조
  - **Trigger:** 로컬 LLM 통합 시작 전
  - **Approach:** 단순 옵션은 `multiprocessing.Manager().Value('i', 0)`, 분산 옵션은 Redis + `aioredis`

- [ ] **[D10] `server/agent_status.py` `_state` 딕셔너리 TTL/최대 크기 안전장치**
  - **What:** hook의 `stop` 이벤트 없이 프로세스가 죽으면(SIGKILL 등) `_state` 항목이 서버 수명 내내 남는 문제 방지 — TTL 만료 또는 최대 크기 제한 추가
  - **Why:** 매우 장기 실행 서버에서 느리게 누적되는 메모리. 당장 위험하진 않지만 스크롤백 바이트 예산·WS 카운터 등 다른 곳엔 이미 있는 방어가 여기만 없음
  - **Context:** 2026-08-18 plan-eng-review(서브에이전트: PTY/tmux/터널) 확인. `server/agent_status.py` 참조
  - **Trigger:** 실제 메모리 증가가 관측되거나 다음 이 파일 손댈 때

- [ ] **[D11] 인증 남은 테스트 커버리지 확장**
  - **What:** `server/auth.py`의 세션 만료, OTP 재전송/재시도 방지, `otp_lock_remaining` 타이밍 등 핵심 3개(변조 HMAC 거부/기기 폐기→세션 무효화/티켓 1회성) 이후 남은 브랜치 테스트
  - **Why:** 2026-08-18 리뷰에서 인증 파일 전체가 무테스트였음이 확인되어 핵심 3개는 즉시 작성하기로 했고, 나머지는 회귀 안전망으로 후속 작업
  - **Context:** `server/tests/test_auth.py`(신규) 참조. plan-eng-review 2026-08-18
  - **Trigger:** 다음 auth.py 변경 전

- [ ] **[D12] `routes/ports.py`/`routes/files.py` HTTP 레벨 라우트 테스트**
  - **What:** FastAPI TestClient로 예외→상태코드 매핑(403/404/409/428) end-to-end 검증. 현재는 하위 로직(`portscan.py`/`fsguard.py`)만 단위 테스트됨
  - **Why:** 라우터 레이어에서 매핑이 깨져도 현재 테스트가 못 잡음
  - **Context:** plan-eng-review 2026-08-18 (서브에이전트: 코드뷰어/포트)
  - **Trigger:** 다음 이 두 라우트 파일 손댈 때

- [ ] **[D13] 프론트엔드 테스트 부재 — theme.js/grid.js/terminal.js**
  - **What:** 스킨 전환(모든 터미널 갱신 여부), `ansiToHtml` XSS escape, 그리드 카드 상태 전이, 탭 생명주기에 대한 단위 테스트. 현재 `frontend/tests/`엔 `keyseq.test.js`/`difflex.test.js`/`sw-push.test.js`뿐
  - **Why:** 특히 `ansiToHtml`은 XSS 방어선인데 회귀 감지 수단이 없음
  - **Context:** plan-eng-review 2026-08-18 (서브에이전트: 프론트엔드)
  - **Trigger:** 다음 이 파일들 크게 손댈 때

- [ ] **[D14] 비밀번호 재시도 락아웃 (OTP와 동일하게)**
  - **What:** `server/auth.py`의 OTP는 `otp_lock_remaining`/`otp_note_failure`로 실패 횟수 제한이 있는데 비밀번호 경로엔 없음. scrypt 자체가 시도당 비용을 부과하지만 무제한 시도 자체는 남아있음
  - **Why:** 같은 파일 안에서 두 자격증명 종류가 다른 위협모델 취급을 받는 일관성 문제
  - **Context:** plan-eng-review 2026-08-18 (서브에이전트: 인증/보안). `server/auth.py:368-390` 참조
  - **Trigger:** 원격 노출 강화 작업 시 함께

- [ ] **[D15] OTP 실패 락 범위를 전역이 아니라 기기/IP 단위로 축소**
  - **What:** 현재 `_otp_failures`가 프로세스 전역 리스트라, 한 클라이언트의 실패한 OTP 시도가 모든 신규 기기 등록을 10분간 잠금. 기기/IP별로 분리
  - **Why:** 스크립트화된 재시도나 오설정 클라이언트 하나가 본인의 새 기기 등록까지 막을 수 있음(가용성 문제)
  - **Context:** plan-eng-review 2026-08-18 (서브에이전트: 인증/보안). `server/auth.py:364-379` 참조
  - **Trigger:** 다중 기기 등록 흐름 다시 손볼 때

## 테스트

- [x] **E2E 테스트 인프라 (Playwright)** ✅ 완료 (커밋 다음, 2026-05-07)
  - **What:** 그리드 뷰 토글·카드 클릭·탭 복원·WS 연결 한도 등 E2E 시나리오
  - **Why:** 현재 18개 단위 테스트가 있지만 UI 흐름은 브라우저 테스트만 검증 가능
  - **Context:** `tests/` 디렉토리 이미 있음. `playwright install chromium`으로 시작 가능
  - **Approach:** `playwright pytest` 플러그인. 서버 픽스처로 uvicorn 인메모리 실행

## 음성 UX

- [ ] **[V1] 핸즈프리 버튼 제거 + 음성바 정리**
  - **What:** 모바일 보이스바에서 🔄 핸즈프리 버튼 제거. `voice.js`의 `handsFreeModeOn` 상태/`toggleHandsFree`/`sendAudio`의 자동 재시작 분기 제거. `index.html:192-195` 버튼 제거 및 CSS 정리
  - **Why:** 현재 "핸즈프리"는 VAD/무음감지가 없어 매 발화 후 수동 stop이 필요한 "자동 재시작 모드". 이름과 실제 동작이 어긋나 사용자 혼동. 진짜 핸즈프리는 VAD 도입이 필요한 별도 큰 작업이라 일단 제거가 맞음
  - **Context:** 2026-05-09 음성 모드 동작 분석에서 확인. `frontend/voice.js:13,87-89,174-187` + `frontend/index.html:192-195`
  - **Approach:** 버튼 + JS 핸들러 + 상태 변수 제거. 보이스바는 음성입력(🎤) + 음성전용(🎧) + 파일(📎) 3개로 정리. 라벨/grid-template 너비 재조정

- [ ] **[V2] 이어폰 Play/Pause 트리거 ON/OFF 토글**
  - **What:** 무선 이어폰의 재생/일시정지 버튼이 음성 입력을 트리거할지 OS 기본 동작(음량/재생 제어)을 할지 사용자가 토글. 모바일·데스크톱 양쪽
  - **Why:** 현재는 한번 활성화되면 끌 방법이 없어 음악 듣다 일시정지 시 의도치 않게 녹음 시작됨. 매우 짜증 포인트
  - **Context:** 2026-05-09 음성 UX 리뷰에서 사용자 요청. 모바일은 `frontend/voice.js:296-352`(Media Session API + 무음 오디오), 데스크톱은 `server/voice_daemon.py:33-88`(NSEvent 글로벌 모니터)
  - **Approach:**
    - 모바일: 보이스바에 토글 버튼 추가(예: 🎵 라벨). OFF 시 `navigator.mediaSession.setActionHandler('play', null)` + `setActionHandler('pause', null)` + `silentAudio.pause()`로 OS에 미디어 컨트롤 양보. 상태는 `localStorage`에 저장
    - 데스크톱: `vt voice` CLI에 `--media-keys` / `--no-media-keys` 옵션 또는 환경변수 `VT_VOICE_MEDIA_KEYS=on/off`. 데몬 측 `_start_media_key_listener` 호출 가드. 실행 중 토글은 별도 옵션(추후)
    - 디폴트: ON (현재 동작 유지) — 마이그레이션 매끄럽게

- [ ] **[V3] (오픈) 멀티 pane 환경에서 음성 타깃 명료화** — 아이디어 모집 중
  - **What:** Voice Daemon이 "tmux의 most-recent active pane"으로 보내는 현재 동작을 유지하되, 사용자가 어디로 갈지 *항상* 알 수 있고 *원할 때 빠르게* 다른 pane으로 변경할 수 있는 메커니즘
  - **Why:** OS 포커스가 노션/브라우저에 있을 때도 daemon은 동작하지만 어느 tmux pane이 타깃인지 사용자가 알 수 없음 → 잘못된 pane에서 명령이 실행될 위험. lock(고정 타깃)은 "여러 터미널 왔다갔다" 시나리오와 충돌해 거부됨
  - **Context:** 2026-05-09 사용자 피드백. `server/voice_daemon.py:162-185`의 `get_active_tmux_pane`/`get_any_tmux_pane`이 핵심. 사용자도 명확한 답을 아직 못 정함
  - **검토 중인 후보:**
    - **N. 항상 표시(Indicator)** — tmux status line 또는 macOS 메뉴바에 "🎤 → dev:0.0" 항시 노출. lock 없음, 비용 낮음
    - **G. 발화 직전 단발 안내** — 핫키 직후 1초간 "→ dev:0.0" osascript notification + (선택) 짧은 카운트다운 동안 ESC로 취소 가능
    - **Q. 세션별 보조 핫키** — Ctrl+Shift+V는 most-recent(현재), Ctrl+Shift+1/2/3은 N번째 세션 명시. 파워 유저 옵션
    - **N+G 조합 (현재 1순위 후보)** — 항시 표시 + 발화 직전 즉시 피드백
  - **거부된 안:** A(고정 lock — 왔다갔다와 충돌), C(TTS 멘트에 pane 정보 — 확인이 사후), H(매번 popup 선택 — 마찰), E(음성 prefix `@dev` — STT 정확도 위험), J(음성으로 타깃 변경 — 명령 모드 구분 어려움)
  - **Trigger:** V1/V2 완료 후 또는 사용자가 방향 결정한 시점
