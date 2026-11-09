# fsh help hotkeys — 핫키 전체 목록 + 변경 방법

## 디폴트 핫키

### 데스크톱 (Voice Daemon, `fsh voice` 실행 시)

| 키 | 동작 |
|---|---|
| `Ctrl+Shift+M` | 녹음 토글 (시작/종료) |
| 이어폰 Play/Pause | 녹음 토글 (`Ctrl+Shift+M`과 동일) |

### 웹 UI (모바일/브라우저)

| 액션 | 동작 |
|---|---|
| 마이크 버튼 클릭 | 녹음 토글 |
| 음성 전용 버튼 클릭 | UI 모드 전환 (터미널 숨김) |
| 팔레트 `>` 「이어폰 미디어키」 | 미디어 키 트리거 ON/OFF (새로고침하면 꺼짐) |
| 이어폰 Play/Pause | 녹음 토글 (이어폰 트리거 ON일 때) |
| `Cmd+F` / `Ctrl+Shift+F` | 스크롤백 검색 |
| `Alt+Shift+F` | 터미널 안 검색 (맥도 같다 — 예전 `Cmd+Shift+F`) |
| `Cmd+Shift+F` / `Ctrl+Shift+G` | 파일 내용 검색 — 사이드바 「파일 › 내용 검색」. 팔레트에서는 `?` 접두어 |
| `Cmd+S` / `Ctrl+S` (뷰어 편집 중) | 파일 저장 — 처음 한 번 웹 로그인 비밀번호를 묻는다(15분 유지). 자동 저장은 없다 |
| `Cmd+.` / `Alt+Shift+.` | 집중 모드 켜기/끄기 — 막대를 모두 숨기고 칸을 창 끝까지 넓힘(설정 → 키맵에서 바꿀 수 있음) |
| 팔레트 `?` 모드 안 `⌥C` · `⌥W` · `⌥R` | 대소문자 구분 · 단어 단위 · 정규식 켜고 끄기 (`⌥Enter` 사이드바로 옮기기) |
| `Cmd+B` / `Alt+Shift+B` | 사이드바 접기/펴기 (설정 → 키맵의 「사이드바 토글」) |
| `Cmd+K` | 빠른 열기(팔레트) |
| `Cmd+,` | 설정 |
| 사이드바 「세션」 행의 `⋯` 또는 우클릭 | 세션 메뉴 (열기/닫기, 이름 변경, 맥에서 열기, 연결된 화면, 종료) |

## 핫키 변경

    fsh hotkey list                   # 현재 설정 확인
    fsh hotkey set voice ctrl+shift+x # 변경
    fsh hotkey reset voice            # 디폴트로 복구
    fsh hotkey disable voice          # 비활성화

내부적으로 `~/.vt.env`에 다음 변수 저장:

    VT_HOTKEY_VOICE="ctrl+shift+x"
    VT_HOTKEY_VOICE_DISABLED="false"

변경 후 daemon 재시작 필요:

    fsh restart --voice

## 키 토큰 형식

`+`로 modifier와 키 결합:

| 토큰 | 의미 |
|---|---|
| `ctrl`, `control` | Control |
| `shift` | Shift |
| `alt`, `option`, `opt` | Alt/Option |
| `cmd`, `command`, `meta`, `super` | Cmd/Win/Super |
| `v`, `a`, `1`, `2`, ... | 글자/숫자 키 |
| `f1` ~ `f12` | Function 키 |

예시:
- `ctrl+shift+m` (기본값)
- `cmd+shift+m`
- `alt+f1`

## 환경별 주의

### macOS
- 시스템 설정 → 개인정보 → 접근성에서 터미널 앱 허용 필요
- macOS 시스템 단축키와 충돌 시 silent 실패

### Linux
- X11에서는 정상 동작
- **Wayland에서는 글로벌 핫키 캡처가 보안 정책상 제한됨** — `XDG_SESSION_TYPE=wayland`이면 daemon이 경고 출력. 모바일 🎤 버튼 또는 X11 세션 사용 권장

### WSL2
- WSLg(Windows 11) 또는 X11 필요. 없으면 모바일 🎤로 대체

## 미디어 키 (이어폰) 토글

이어폰 Play/Pause를 음성 입력 트리거로 쓰지 않으려면:

### 모바일 (브라우저)
팔레트(`Cmd+K`)에서 `>` 「이어폰 미디어키」로 끄고 켭니다 → OFF 시 OS가 기본 미디어 컨트롤 가져감 (음악 재생/일시정지 정상 동작). 페이지를 새로 열면 늘 OFF로 시작합니다

### 데스크톱 (`~/.vt.env`)
    VT_VOICE_MEDIA_KEYS="off"

이후 `fsh restart --voice`로 daemon 재시작.
