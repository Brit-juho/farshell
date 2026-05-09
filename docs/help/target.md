# vt help target — 음성 타깃 lock 설명

Voice Daemon(`vt voice` 데스크톱 핫키)이 어느 tmux 세션으로 입력을 보낼지 제어합니다.

## 두 가지 모드

### AUTO (디폴트)
- tmux의 `most-recent active pane`으로 자동 발사
- 사용자가 마지막에 attach/입력했던 세션으로 감
- 여러 세션 사이 자유롭게 전환 가능
- 단점: 어디로 갈지 사용자가 즉시 알기 어려움

### LOCK
- 명시적으로 한 세션에 고정
- 다른 세션이 활성이어도 음성은 lock된 세션으로만
- 안전성 ↑

## 명령

    vt voice-target              # 현재 모드 + 사용 가능한 세션 목록
    vt voice-target dev          # dev 세션으로 LOCK
    vt voice-target --auto       # LOCK 해제, AUTO로 복귀

내부적으로 `~/.vt/voice_target` 파일에 세션명 저장. daemon이 매 발화 시 읽음 (재시작 불필요).

## 동작 시나리오

### 시나리오 A: 노션 작업 중 음성으로 코딩
    vt voice                # daemon 시작
    vt voice-target dev     # dev에 lock
    [노션에서 작업]
    [Ctrl+Shift+V] "git status"
    → 항상 dev 세션의 active pane으로 입력

### 시나리오 B: 여러 터미널 왔다갔다
    vt voice                # daemon 시작
    [기본 AUTO]
    [Ctrl+Shift+V] "ls"     → 마지막 본 세션으로
    [iTerm에서 p9 attach]
    [Ctrl+Shift+V] "npm run" → 이제 p9으로

### 시나리오 C: 빌드 중인 세션으로 잘못 가는 거 방지
    vt voice-target dev     # dev에 lock
    [p9에서 npm test 돌리며 다른 세션 탐색]
    [Ctrl+Shift+V] "next"
    → p9로 안 감 (test 세션 안전), dev로 감

## lock된 세션이 없어진 경우

세션이 kill되면 daemon이 자동으로 AUTO로 폴백. 로그에 warning 출력.
복구하려면 새 세션 만들고 다시 lock.

## 모바일은?

모바일은 "현재 보고 있는 탭 = 타깃"이라 별도 lock 불필요.
탭 전환만으로 타깃 변경 자연스럽게 됨.
