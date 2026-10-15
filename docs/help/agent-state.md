# 에이전트 상태 (`fsh hooks` · `fsh pane report`)

FarShell은 각 tmux 세션이 지금 무슨 상태인지 서버가 판정해서 웹/모바일 UI에
표시한다. 상태는 4가지다.

| 상태 | 뜻 | 어떻게 들어오나 |
|---|---|---|
| `idle` | 아무것도 안 함 | 초기값 · 완료 확인(ack) · TTL 만료 |
| `working` | 프롬프트 처리·도구 실행 중 | Claude Code/Codex 시작 훅 |
| `waiting` | 승인·입력 대기 | Claude Code·Codex 승인 훅 또는 출력 패턴 감지 |
| `done` | 응답·세션 완료 | Claude Code/Codex 완료 훅 |

## Claude Code · Codex — 훅으로 자동

```bash
fsh hooks status      # 등록 상태 확인 (fsh doctor 에도 표시된다)
fsh hooks install     # 등록/갱신 (멱등, 직접 넣은 다른 훅은 보존)
fsh hooks uninstall   # FarShell 항목만 제거
fsh hooks dedupe      # ~/.codex/hooks.json 에 복제된 FarShell 훅 제거 (--dry-run 으로 미리보기)
```

`./install.sh`가 설치 시 대신 실행한다. **등록되지 않으면 상태 배지도, 프롬프트
큐 자동 투입도 동작하지 않는다. Claude Code 완료 알림(TTS)도 훅이 필요하다** — 그런데 눈에 띄게 실패하는
게 아니라 조용히 아무 일도 안 일어나므로, 뭔가 안 뜬다면 여기부터 확인한다.

Claude Code의 `~/.claude/settings.json`에는 도구 시작·종료, 완료, 세션 시작,
알림, 승인 요청의 6종이,
Codex의 `~/.codex/config.toml`에는 prompt/permission/tool/stop/session 6종이 등록된다.
두 설정 모두 FarShell 항목만 관리하고 기존 훅은 보존한다.

**Codex 앱의 「Claude 설정 가져오기」를 눌렀다면** `~/.codex/hooks.json`에 Claude의
FarShell 훅(`agent_hook.sh … claude`)이 복제된다. Codex는 `config.toml`과 `hooks.json`을
모두 읽으므로 한 이벤트가 두 번(그중 하나는 `claude` 태그로) 들어와 에이전트 라벨이
어긋난다. `fsh hooks status`/`fsh doctor`가 이를 경고하고, `fsh hooks dedupe`가
`hooks.json`에서 `agent_hook.sh` 항목**만** 지운다(다른 훅은 보존, 백업
`hooks.json.vtbak`, 멱등). FarShell은 `hooks.json`에 쓰지 않는다.
`tts_hook.sh`를 `Stop`에 **직접** 걸면 안 된다 — `agent_hook.sh stop`이 내부에서
그걸 호출하므로 TTS가 두 번 재생된다.

## 그 외 에이전트 — 직접 보고

aider·agy처럼 관리형 훅이 없는 에이전트는 그 pane에서 직접 알린다. Codex도
훅을 설치하지 않은 환경에서는 같은 명령을 임시 폴백으로 쓸 수 있다.

```bash
fsh pane report --state working --agent codex
fsh pane report --state done
```

tmux 안에서 실행하면 `$TMUX_PANE`을 함께 보내 **정확히 그 pane**으로 매칭된다.
tmux 밖이거나 pane id를 못 쓰는 상황이면 현재 디렉토리(cwd)로 매칭하는데, 같은
디렉토리에 세션이 둘 이상이면 **아무것도 표시하지 않는다** — 엉뚱한 세션에
배지를 띄우는 것보다 안전하기 때문이다. 명령 결과 메시지가 어느 방식으로
매칭됐는지 알려준다.

## 어떻게 매칭하나 (3단)

1. **pane 자기보고** — Claude 훅/CLI가 보낸 `$TMUX_PANE`을 서버의 pane 목록과 정확 매칭
2. **cwd 일치** — 1이 없을 때. 후보가 정확히 하나일 때만
3. **포기** — 아무것도 강조하지 않는다

FarShell은 `-L fsh` 격리 소켓을 쓴다. 개인 tmux(`default` 소켓)에서 에이전트를
띄웠다면 pane id가 우리 소켓의 다른 pane과 우연히 겹칠 수 있으므로, `$TMUX`의
소켓 이름이 우리 것일 때만 pane id를 신뢰하고 아니면 버리고 2단계로 내려간다.

Codex 훅은 공유 app-server에서 오래된 pane 값을 받을 수 있어 pane 대신 cwd를
사용합니다. cwd가 정확히 한 세션에 일치할 때만 연결하고, 이후 같은 Codex
`session_id`의 이벤트는 그 연결을 유지합니다. 자세히는 `docs/ref/agents.md`.

## 상태가 안 풀릴 때

훅이 유실되면(네트워크 실패 등) 상태가 멈춘 채로 남을 수 있어 TTL이 있다.
`working` 15분 → `idle`, `waiting` 2분 → `working`, `done` 30분 → `idle`.
값은 `VT_AGENT_TTL_WORKING` / `_WAITING` / `_DONE`(초)로 바꿀 수 있다.

## 위임 — 에이전트가 하위 세션에 일을 맡기기 (MCP)

에이전트가 `spawn_session`으로 하위 세션을 만들어 일을 맡기고, 결과를 `[위임 결과 …]` 입력으로 돌려받는다.
하위 세션은 기본 권한 모드로 떠서 승인 요청이 폰에 뜨고, 사람은 레일에서 그 세션을 열어 언제든 끼어들 수 있다.
하위 세션은 다시 하위 세션을 만들 수 없고, 열린 하위 세션은 부모당 3개까지다. 끄려면 `VT_AGENT_SPAWN=0`.
목록·닫기: `fsh delegate ls`, `fsh delegate close <id>`(세션은 남긴다).

에이전트가 권한을 요청하거나(`request_access`) 어떤 권한이 있는지 보고(`whoami`, `list_my_grants`), 사람은 승인함에서 «한 번만 / 이 세션 동안 / 거부»를 고른다. 터미널의 `fsh authz` 는 조회 전용이다(승인·회수는 웹/폰).

## 에이전트에게 세션 공유하기 (MCP)

에이전트는 사용자가 만든 세션을 만들거나 닫을 수 없다(자기 하위 세션은 아래 「위임」으로 만든다). 사용자가 평범한 세션(빈 터미널)을 만들고 닉네임을 붙여
**공유**하면, 에이전트가 MCP(`claude mcp add farshell -- fsh mcp`)로 그 세션만 쓸 수 있다.

| 도구 | 하는 일 |
|---|---|
| `list_shared_sessions` | 공유된 세션과 내 권한(읽기/쓰기) 상태 |
| `read_session` | 화면의 마지막 줄들(기본 200, 최대 2000줄 · 64KB)을 읽는다 |
| `send_to_session` | 한 줄을 입력한다. `enter: true`일 때만 Enter. 읽기·쓰기로 공유된 세션만 |

- 처음 쓰면 **사용자 승인**이 필요하다. 에이전트는 「승인 대기 중」을 받고, 사용자가 FarShell에서
  승인하면 같은 호출을 다시 한다. 읽기와 쓰기는 따로 승인하고, 한 번 승인하면 그 에이전트가
  같은 tmux 세션에 있는 한(`/new`·`/clear`·재시작 포함) 계속 유효하다. 어느 쪽 세션이든 끝나면 사라진다.
- 승인·공유·철회는 **브라우저에 로그인한 사람만** 한다. 기계 토큰으로는 안 된다.
- 읽기 전용 공유에 쓰기를 요청하면 묻지 않고 거부한다. 공유 모드를 낮추면 바로 적용된다.
- 입력은 한 줄·4000자 이하, 제어 문자와 줄바꿈은 거부한다. 에이전트 CLI가 승인 대기(`waiting`)
  또는 작업 중(`working`)인 세션에는 보내지 않는다.
- 모든 접근은 `~/.vt/agent_access.log`에 남는다(글 원문은 없고 글자 수와 해시 앞 8자리만).
- 호출한 에이전트가 정말 그 tmux 칸에서 실행 중인지 서버가 pane과 부모 프로세스로 검증한다.
  tmux 밖에서 실행한 에이전트는 식별할 수 없어 거부된다.
