# vt help hotkeys — 핫키 전체 목록 + 변경 방법

## 디폴트 핫키

### 데스크톱 (Voice Daemon, `vt voice` 실행 시)

| 키 | 동작 |
|---|---|
| `Ctrl+Shift+V` | 녹음 토글 (시작/종료) |
| 이어폰 Play/Pause | 녹음 토글 (`Ctrl+Shift+V`와 동일) |

### 웹 UI (모바일/브라우저)

| 액션 | 동작 |
|---|---|
| 마이크 버튼 클릭 | 녹음 토글 |
| 음성 전용 버튼 클릭 | UI 모드 전환 (터미널 숨김) |
| 이어폰 버튼 클릭 | 미디어 키 트리거 ON/OFF |
| 이어폰 Play/Pause | 녹음 토글 (이어폰 트리거 ON일 때) |
| `Cmd+F` / `Ctrl+F` | 터미널 검색 |
| 탭 더블클릭 | 세션 이름 편집 |

## 핫키 변경

    vt hotkey list                   # 현재 설정 확인
    vt hotkey set voice ctrl+shift+x # 변경
    vt hotkey reset voice            # 디폴트로 복구
    vt hotkey disable voice          # 비활성화

내부적으로 `~/.vt.env`에 다음 변수 저장:

    VT_HOTKEY_VOICE="ctrl+shift+v"
    VT_HOTKEY_VOICE_DISABLED="false"

변경 후 daemon 재시작 필요:

    vt stop && vt voice

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
- `ctrl+shift+v` (디폴트)
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
보이스바의 🎵 이어폰 버튼 클릭 → OFF 시 OS가 기본 미디어 컨트롤 가져감 (음악 재생/일시정지 정상 동작)

### 데스크톱 (`~/.vt.env`)
    VT_VOICE_MEDIA_KEYS="off"

이후 `vt stop && vt voice`로 daemon 재시작.
