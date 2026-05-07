# TODOS

작업 항목 목록. 완료 시 `- [x]` 표시.

---

## 아키텍처

- [ ] **[D6] `server/main.py` routes/ 분리**
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

- [ ] **E2E 테스트 인프라 (Playwright)**
  - **What:** 그리드 뷰 토글·카드 클릭·탭 복원·WS 연결 한도 등 E2E 시나리오
  - **Why:** 현재 18개 단위 테스트가 있지만 UI 흐름은 브라우저 테스트만 검증 가능
  - **Context:** `tests/` 디렉토리 이미 있음. `playwright install chromium`으로 시작 가능
  - **Approach:** `playwright pytest` 플러그인. 서버 픽스처로 uvicorn 인메모리 실행
