# fsh help terminal — 메인 터미널과 창 열기

fsh가 새 터미널 창을 열 때 **어느 앱을 쓸지 사용자가 고른다.** 특정 앱으로 고정돼 있지 않다.
값은 `~/.vt.env`의 `VT_TERMINAL`에 저장되고, CLI와 웹이 같은 구현(`server/terminal_open.py`)을 쓴다.

```bash
fsh terminal                  # 현재 설정 + 설치된 앱
fsh terminal set ghostty      # auto | ghostty | iterm | terminal | wezterm | warp | kitty | alacritty
fsh terminal unset            # 자동 선택으로 되돌리기
```

- `auto`(기본): iTerm2 → Ghostty → WezTerm → Kitty → Alacritty → Terminal.app 중 설치된 첫 앱
- 고른 앱이 설치돼 있지 않으면 `set`이 거부한다. 실행 중 열지 못하면 알린 뒤 자동 선택으로 넘어간다.
- Ghostty(1.3+)는 이미 켜진 앱에 창만 추가한다(AppleScript). 처음 쓸 때 macOS가
  「자동화」 권한을 물으면 허용한다. 거부하면 `open -na`로 폴백하지만 더 느리다.
- Warp는 명령을 자동 실행하지 못해 앱만 열고 명령을 클립보드에 복사한다(붙여넣기 필요).
- Linux는 설치된 터미널 에뮬레이터를 자동으로 고른다(`VT_TERMINAL` 지정 없음).

## 창을 여는 곳

| 어디서 | 동작 |
|---|---|
| `fsh attach [세션]` | 그 tmux 세션을 새 창에 붙인다 |
| `fsh handoff desktop` | 폰에서 보던 `dev` 세션을 맥 터미널로 가져온다 |
| 웹 「맥에서 열기」 | 서버가 있는 맥에 새 창을 연다 (같은 구현) |
| 웹 설정 「새 세션 생성 시 맥 터미널에서도 열기」 | 새 세션을 만들 때마다 창을 연다 (기본 꺼짐) |

- **창 제목은 세션 이름**이다(`server`, `dev` …). fsh 전용 tmux 서버에 `set-titles`를 켜서 만든다.
- **세션당 창 하나**: 이미 창이 붙어 있는 세션은 새로 열지 않는다.
- 명령을 `exec`로 실행하므로 tmux 세션이 끝나면 터미널 앱이 창을 닫는다. 단 **Ghostty는 AppleScript로 연 창이 프로세스가 끝나도 남는다**(직접 닫는다). 참고로 tmux의 `detach-on-destroy off` 설정이면 세션을 지워도 클라이언트가 다른 세션으로 옮겨 갈 뿐 창이 닫히지 않는다.
- `fsh start`/`restart`/`mobile`/`voice`는 창을 열지 않는다.
- 새 창이 안 열리면: 출력된 수동 명령(`tmux -L fsh new -A -s dev`)을 아무 터미널에서 실행하고,
  `fsh doctor`의 「터미널 앱」 항목과 macOS 자동화 권한을 확인한다.
