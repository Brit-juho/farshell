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
파싱이 실패한 파일은 **그 파일만** 건너뛴다(잘못된 정규식 하나가 서버 전체의
승인 대기 감지를 죽이면 안 된다).

## 포맷

```toml
# codex — 패턴 미조사. 채우면 그 즉시 waiting 감지가 켜진다(서버 재시작 불필요).
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
| `options` | 정규식(문자열 또는 문자열 목록). 캡처 그룹 1=번호, 2=라벨. 매치되면 모바일 플릿 홈에 번호 버튼이 뜬다. 실패해도 `enter`/`exit`는 그대로 산다 |

`enter`/`exit`가 둘 다 비어 있으면(현재 codex/aider/gemini 스텁 상태) 그 CLI는
`/api/agents/coverage`에서 `path: "none"`, `trust: "low"`로 보고된다 — toml
파일은 있지만 실효 패턴이 없다는 뜻이다.

## `enter`/`exit` 정규식 쓸 때 주의

- ⚠ **오탐 주의**: 빌드 로그·diff에 우연히 들어갈 수 있는 짧은 문자열
  (`"Yes"`, `"y/n"`)은 쓰지 않는다. 실제 프롬프트에만 나오는 긴 문구를 고른다.
- `enter`/`exit`는 **리터럴 바이트 문자열**로 매칭한다(정규식이 아니다) —
  `agent_prompt_detect.load_patterns()`가 `str.encode()`로 그대로 쓴다.
  정규식이 필요한 건 `options` 하나뿐이다.
- 실제 터미널 출력에는 커서 이동·색상·줄지우기 ANSI 코드가 섞여 들어온다.
  `options` 정규식을 짤 때는 이미 ANSI가 제거되고 줄바꿈이 `\n`으로 통일된
  텍스트(`_strip_ansi()` 결과)를 대상으로 매칭한다는 걸 가정하면 된다 —
  직접 이스케이프 시퀀스를 정규식에 넣을 필요는 없다.

## codex/gemini/aider를 채우지 않는 이유(현재 상태)

2.1.1 계획(`80-multihost-agents.md` §2)은 이 세 CLI의 실제 출력 문구를
**추측해서 넣지 말라**고 명시한다 — 틀린 패턴은 오탐(엉뚱할 때 waiting 표시)
또는 미탐(진짜 승인 대기인데 못 잡음)으로 이어지고, 둘 다 사용자를 잘못
가이드한다. 실제 CLI를 돌려서 나온 샘플 출력을 받은 뒤에만 채운다.

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
수도 있다(codex/aider/gemini가 실제로 쓰는 경로):

```bash
fsh pane report --state working --agent codex
fsh pane report --state done
```

tmux 안에서 실행하면 `$TMUX_PANE`으로 정확히 그 pane에 매칭된다. 매칭 방식은
`fsh help agent-state`의 "3단 해석"과 같다.

**3. toml을 고치고 즉시 반영 확인**

```bash
# 임시로 라인을 하나 늘려도 서버 재시작 없이 즉시 반영되는지
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
