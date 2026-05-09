# TODOS

작업 항목 목록. 완료 시 `- [x]` 표시.

---

## 아키텍처

- [x] **[D6] `server/main.py` routes/ 분리** ✅ 완료 (커밋 111d2dc, 2026-05-07)
  - **What:** main.py 936줄 / 37라우트를 `routes/pty.py`, `routes/tmux.py`, `routes/voice.py`, `routes/tunnel.py`로 분리
  - **Why:** 새 라우트 추가마다 단일 파일이 커져 코드 탐색 비용 증가
  - **Context:** 2026-05-07 plan-eng-review(D6)에서 확인. 현재는 관리 가능 수준(936줄)이나 다음 큰 기능 추가 전 진행 권장
  - **Approach:** FastAPI `APIRouter` + `app.include_router()`로 점진적 이동. 기존 엔드포인트 경로 변경 없음

## 인프라

- [ ] **[D1] 멀티워커 환경에서 WS 연결 카운터 공유**
  - **What:** `_ws_total_count` / `_ws_count_per_session`을 프로세스 간 공유 (Redis 또는 `multiprocessing.Value`)
  - **Why:** `uvicorn --workers N` 사용 시 프로세스별 독립 카운터로 연결 한도가 무의미해짐
  - **Context:** 로컬 LLM 전환 시 멀티워커가 필수. 현재 단일 워커라 문제 없음. `server/main.py:530-531` 참조
  - **Trigger:** 로컬 LLM 통합 시작 전
  - **Approach:** 단순 옵션은 `multiprocessing.Manager().Value('i', 0)`, 분산 옵션은 Redis + `aioredis`

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
