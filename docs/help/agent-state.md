# 에이전트 상태 (`fsh hooks` · `fsh pane report`)

FarShell은 각 tmux 세션이 지금 무슨 상태인지 서버가 판정해서 웹/모바일 UI에
표시한다. 상태는 4가지다.

| 상태 | 뜻 | 어떻게 들어오나 |
|---|---|---|
| `idle` | 아무것도 안 함 | 초기값 · 완료 확인(ack) · TTL 만료 |
| `working` | 도구 실행 중 | Claude Code `PreToolUse` 훅 |
| `waiting` | 승인·입력 대기 | 출력 패턴 감지 (준비 중) |
| `done` | 응답 완료 | Claude Code `Stop` 훅 |

## Claude Code — 훅으로 자동

```bash
fsh hooks status      # 등록 상태 확인 (fsh doctor 에도 표시된다)
fsh hooks install     # 등록/갱신 (멱등, 직접 넣은 다른 훅은 보존)
fsh hooks uninstall   # FarShell 항목만 제거
```

`./install.sh`가 설치 시 대신 실행한다. **등록되지 않으면 상태 배지도, 프롬프트
큐 자동 투입도, TTS 요약도 전부 동작하지 않는다** — 그런데 눈에 띄게 실패하는
게 아니라 조용히 아무 일도 안 일어나므로, 뭔가 안 뜬다면 여기부터 확인한다.

`~/.claude/settings.json`에 등록되는 것은 `agent_hook.sh {pre,post,stop}` 3종이다.
`tts_hook.sh`를 `Stop`에 **직접** 걸면 안 된다 — `agent_hook.sh stop`이 내부에서
그걸 호출하므로 TTS가 두 번 재생된다.

## 그 외 에이전트 — 직접 보고

codex·aider·gemini에는 Claude Code 같은 훅이 없다. 그 pane에서 직접 알린다.

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

1. **pane 자기보고** — 훅/CLI가 보낸 `$TMUX_PANE`을 서버의 pane 목록과 정확 매칭
2. **cwd 일치** — 1이 없을 때. 후보가 정확히 하나일 때만
3. **포기** — 아무것도 강조하지 않는다

FarShell은 `-L fsh` 격리 소켓을 쓴다. 개인 tmux(`default` 소켓)에서 에이전트를
띄웠다면 pane id가 우리 소켓의 다른 pane과 우연히 겹칠 수 있으므로, `$TMUX`의
소켓 이름이 우리 것일 때만 pane id를 신뢰하고 아니면 버리고 2단계로 내려간다.

## 상태가 안 풀릴 때

훅이 유실되면(네트워크 실패 등) 상태가 멈춘 채로 남을 수 있어 TTL이 있다.
`working` 15분 → `idle`, `waiting` 2분 → `working`, `done` 30분 → `idle`.
값은 `VT_AGENT_TTL_WORKING` / `_WAITING` / `_DONE`(초)로 바꿀 수 있다.
