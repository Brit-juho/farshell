# 감지 패턴 (`server/detect/*.toml`)

에이전트 CLI가 "승인/입력을 기다리고 있다"(`waiting`)를 FarShell이 알아내는
두 경로 중 하나가 이 파일들이다(다른 하나는 Claude Code 훅 — `fsh help
agent-state`). PTY 출력 스트림에 문자열이 나타나는지만 보는 **정규식 매칭**이라
어떤 CLI든 붙일 수 있지만, 실제 문구를 넣기 전에는 아무것도 감지하지 않는다.

설정 → 에이전트 탭의 "감지 커버리지" 표(`GET /api/agents/coverage`)가 지금
CLI별로 무엇이 채워져 있는지 보여준다.

## 왜 파이썬이 아니라 TOML인가

CLI가 업데이트되면 프롬프트 문구가 바뀐다. 그때마다 서버 코드를 고치고
재시작하는 대신, 이 파일 한 줄만 고치면 된다 — `server/agent_prompt_detect.py`의
`load_patterns()`가 매 서버 시작마다(또는 `force=True` 재적재 시) 읽는다.
(2026-10-06 실측: 감지 커버리지 표(`/api/agents/coverage`)는 요청마다 다시 읽지만 PTY 감지기는
캐시를 쓴다 — toml을 고친 뒤 실제 waiting/interrupt 감지에 반영하려면 서버를 재시작해야 한다.)
파싱이 실패한 파일은 **그 파일만** 건너뛴다(잘못된 정규식 하나가 서버 전체의
승인 대기 감지를 죽이면 안 된다).

## 포맷

```toml
# 예시 CLI — 채우면 waiting 감지가 켜진다(PTY 감지기는 패턴을 캐시하므로 서버 재시작 필요).
name = "codex"

enter = [
  "실제 프롬프트에만 나오는 문구 1",
  "실제 프롬프트에만 나오는 문구 2",
]

exit = [
  "프롬프트가 사라졌다는 신호",
]

# 선택 — 번호 선택지 캡처(모바일 인라인 승인용, 70-mobile.md §2)
options = '^\s*[❯>]?\s*(\d+)\.\s+(.+?)\s*$'
```

| 필드 | 의미 |
|---|---|
| `name` | 없으면 파일명(확장자 제외)을 쓴다. `claude.toml` → `claude` |
| `enter` | 문자열 목록. 최근 출력 윈도우(2048바이트)에 하나라도 나타나면 그 세션을 `waiting`으로 본다 |
| `exit` | 문자열 목록. 나타나면 즉시 `waiting` 해제 — 프롬프트가 사라졌다는 신호 |
| `[kinds]` | 표. 키 = waiting 유형(`approval`/`choice`/`plan`/`question`), 값 = 리터럴 문자열 목록(`enter`와 같은 리터럴 바이트 규칙). 여기 적힌 문구도 waiting 진입 신호다. `enter`에 걸리면 `approval`. 같은 윈도우에 여럿이면 plan → approval → enter → choice → question 순으로 이긴다 |
| `ignore_exit_on` | kind 목록(예: `["choice"]`). 그 kind가 떠 있는 동안 `exit`를 무시한다 — Codex 질문 화면은 상태줄 `esc to interrupt`를 같이 그린다. 이 키는 `[kinds]` 표 **위**에 쓴다(TOML 규칙) |
| `prefer_latest_kind` | `true`/`false`(기본 `false`). 서로 다른 대화상자 문구가 출력 이력에 함께 남아 있을 때 **윈도우에서 가장 나중에 나타난 kind**를 고른다(Codex: 승인 문구 위에 계획 문구가 남은 경우). 선택지 번호도 현재 제목 줄 이후에서만 뽑는다. 이 키는 `[kinds]` 표 **위**에 쓴다(TOML 규칙) |
| `interrupt` | 문자열 목록. 사용자가 승인을 **거절**하거나 ESC로 작업을 끊으면 CLI는 Stop 훅을 내지 않고 이 문구만 화면에 남긴다 — 새로 그려지는 순간 그 세션의 `waiting`/`working` 엔트리를 `done`으로 마친다. waiting 진입 신호가 아니다. 문구 뒤에 `enter`/`exit`가 다시 그려졌으면 이미 지난 화면이라 무시한다. `[kinds]` 표 **위**에 쓴다 |
| `interrupt_stale_after` | 정규식(여러 줄 모드). 그 `interrupt` 문구 **뒤**에 맞으면 새 중단이 아니라 이미 지나간 화면의 재그림이라 무시한다. Codex는 중단 줄이 스크롤백에 남아 다음 턴에도 tmux 부분 재그림에 실려 오므로(0.160.0 실측) 뒤따르는 새 입력(`› `)·응답(`• `) 줄로 가른다. 없으면 문구만 본다. `[kinds]` 표 **위**에 쓴다 |
| `[badge]` | 표. 키 = 보조 표시 값, 값 = 리터럴 목록. 걸려도 waiting으로 올리지 않고 엔트리에 `badge`만 붙인다(agy `/artifact to review` -> `plan`). 사용자 입력이 오면 뗀다 |
| `options` | 정규식(문자열 또는 문자열 목록; 앞에서부터 처음 매치되는 것만 쓴다). 캡처 그룹 1=번호, 2=라벨 — 이름 그룹 `label`/`key`가 있으면 그것(Codex: 보내는 키가 번호가 아니라 `(y)`/`(a)`/`esc`). 매치되면 모바일 플릿 홈에 번호 버튼이 뜬다. 실패해도 `enter`/`exit`는 그대로 산다 |

`enter`/`exit`가 둘 다 비어 있으면(2026-09-11 이후로는 해당 없음 — 아래
「codex/aider 채움」 참고) 그 CLI는
`/api/agents/coverage`에서 `path: "none"`, `trust: "low"`로 보고된다 — toml
파일은 있지만 실효 패턴이 없다는 뜻이다.

## kind 판별 — CLI별 spec 격리

`kinds`/`enter`는 **detector가 판별한 그 CLI의 toml로만** 매칭한다(훅/자기보고가 박은
엔트리의 `agent` 우선, 없으면 프로세스 스캔). agy와 Claude Code가 둘 다
`Do you want to proceed?`를 그리므로 격리하지 않으면 한쪽 문구가 다른 쪽을 대기로
올린다. toml 없는 CLI는 대기로 올리지 않는다. 판별 못 하면(셸, ssh 등) 옛 동작대로 모든
toml을 본다. 우선순위는 훅 kind > 화면 kind > 폴백(`question`).
`GET /api/agents/coverage`의 `kinds`/`kindSources`가 CLI별로 무엇을 가려내는지 보여준다.

## `enter`/`exit` 정규식 쓸 때 주의

- ⚠ **오탐 주의**: 빌드 로그·diff에 우연히 들어갈 수 있는 짧은 문자열
  (`"Yes"`, `"y/n"`)은 쓰지 않는다. 실제 프롬프트에만 나오는 긴 문구를 고른다.
- `enter`/`exit`/`[kinds]`/`interrupt`/`[badge]`는 **리터럴 문자열**로 매칭한다(정규식이 아니다).
  정규식이 필요한 건 `options` 하나뿐이다.
- 실제 터미널 출력에는 커서 이동·색상·줄지우기 ANSI 코드가 섞여 들어온다. 감지 윈도우는
  **ANSI를 벗기고 NBSP를 공백으로, 줄 머리로 가는 커서 이동(`\x1b[19;1H`)을 `\n`으로** 바꾼
  텍스트다(`_append_clean`) — 직접 이스케이프를 리터럴이나 정규식에 넣을 필요가 없다.
  2026-10-05 실측으로 이유가 드러났다: Claude Code는 `Interrupted · What should ...` 를 단어마다
  색상 코드로 쪼개 그리고, agy는 선택 화면 안내줄(`↑/↓ Navigate · enter Select · esc Skip`)을
  그렇게 그린다. 원본 바이트로 매칭하던 때는 둘 다 실화면에서 안 걸렸다(합성 문자열 테스트만 통과).
  줄이 좁은 PTY(80열)에서 줄바꿈되는 문구(`Would you like to` / `proceed?`)는 한 줄로 안 나오므로
  줄 앞부분만 쓰는 문구를 함께 둔다.

## codex/aider 채움 — 근거와 한계 (2026-09-11)

2.1.1 계획(`80-multihost-agents.md` §2)은 이 세 CLI의 실제 출력 문구를
**추측해서 넣지 말라**고 명시한다. 실제 CLI를 손으로 돌려 샘플을 받는 대신
(사용자 승인하에) 각 CLI의 **공식 오픈소스 저장소 소스 코드**에서 리터럴
문자열을 직접 확인해 채웠다 — 출처·커밋 해시는 각 toml 파일 상단 주석에 있다.

| CLI | enter/exit | 근거 |
|---|---|---|
| `codex` | 채움 | `openai/codex` Rust TUI 소스(`approval_overlay.rs`, `status_indicator_widget.rs`) |
| `aider` | enter만 채움, exit 없음 | `Aider-AI/aider` 소스(`io.py`, `base_coder.py`) — 풀스크린 상태줄이 없어 안전한 exit 리터럴을 못 찾았다(추측 금지 원칙 유지) |

**한계**: 소스 코드 확인이지 실제 터미널 렌더 확인이 아니다 — ANSI 코드·줄
분할 방식까지 검증된 claude.toml만큼의 신뢰도는 아니다(`/api/agents/coverage`
가 이 셋을 `trust: "mid"`로 보고하는 이유). 실제 세션에서 오탐/미탐이 보이면
`fsh pane report`로 캡처해서 이 파일들을 고쳐라. 번호 선택지(`options`)는
처음엔 소스만으로 확신할 수 없어 비워뒀다. codex는 이후 실화면(0.155.0 → 0.160.0, 2026-10-06)으로 채웠고
`interrupt`도 실측했다(`docs/ref/session-view-measurements.md` §1).

## `fsh pane report`로 수동 주입해 테스트하기

서버를 재시작하지 않고 감지 로직을 확인하려면, PTY에 진짜 그 문구가 찍히게
만들거나(예: `printf`로 흉내) 상태 자체를 직접 보고하는 두 가지 방법이 있다.

**1. 패턴 감지 확인 — 실제로 문구를 출력**

```bash
# tmux dev 세션 안에서
printf 'Do you want to proceed?\n'
```

`fsh doctor` 또는 설정 → 정보 화면에서 그 세션이 `waiting`으로 바뀌는지 본다.
`esc to interrupt`를 출력하면 다시 풀린다.

**2. 상태 자체를 직접 주입 — 훅이 없는 CLI 흉내**

`enter`/`exit` 패턴과 무관하게, `working`/`done` 등 상태 자체를 강제로 보고할
수도 있다(codex/aider/agy처럼 훅이 없는 CLI가 쓰는 경로):

```bash
fsh pane report --state working --agent codex
fsh pane report --state done
```

tmux 안에서 실행하면 `$TMUX_PANE`으로 정확히 그 pane에 매칭된다. 매칭 방식은
`fsh help agent-state`의 "3단 해석"과 같다.

**3. toml을 고치고 즉시 반영 확인**

```bash
# 임시로 라인을 하나 늘려도 커버리지 표에는 서버 재시작 없이 반영된다(PTY 감지기는 재시작 필요)
echo '# test' >> server/detect/codex.toml
curl -s http://localhost:7777/api/agents/coverage | python3 -m json.tool
git checkout -- server/detect/codex.toml   # 실험 후 원복
```

`patternLines`가 방금 늘린 줄 수만큼 바뀌어 있으면 정상이다.

## 관련

- `fsh help agent-state` — 4가지 상태(`idle`/`working`/`waiting`/`done`)와
  Claude Code 훅 경로 전체
- `server/agent_prompt_detect.py` — 실제 매칭 로직(윈도우·flap guard·ANSI 제거)
- 설정 → 에이전트 탭 — CLI별 감지 커버리지 표
