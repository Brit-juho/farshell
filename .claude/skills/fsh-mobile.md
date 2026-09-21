---
name: fsh-mobile
description: |
  모바일 기기에서 FarShell 테스트. 접속 URL 발급, adb 포트포워딩, Chrome 열기,
  스크린샷 캡처까지. Use when asked to "모바일 테스트", "폰에서 테스트",
  "mobile test", "adb 테스트", "모바일로 확인".
allowed-tools:
  - Bash
  - Read
  - AskUserQuestion
---

## 모바일 테스트 스킬

`fsh`가 하는 일(서버·터널·URL·QR)은 `fsh`에 맡기고, 여기서는 **adb 쪽 절차**만 다룹니다.

### 1. 서버와 접속 URL 확보

```bash
fsh status     # 서버·터널이 이미 떠 있는지 먼저 본다
fsh mobile     # 접속 URL + QR 코드 (필요하면 서버·터널도 함께 시작)
```

**URL은 `fsh`가 말하는 것을 쓰세요.** `/tmp/cloudflared.log`를 직접 grep하면 제공자가
cloudflare가 아닐 때(ngrok·명명 터널·none) 틀린 답이 나옵니다 — `_main_tunnel_url()`이
단일 진실의 원천이고 `fsh status`/`fsh mobile`이 그걸 씁니다.

서버가 이미 돌고 있는데 코드만 반영하면 되는 상황이면 **`fsh restart`**를 쓰세요.
`fsh stop && fsh start`는 터널을 죽여 접속 URL을 바꿔버리므로, 폰에 열어둔 탭이
그 순간 죽습니다(그런데 화면은 캐시 덕에 멀쩡해 보여서 원인을 못 찾습니다).

### 2. 기기 확인

```bash
adb devices 2>&1
```

기기가 없으면 USB 연결 + USB 디버깅 활성화를 안내하세요.

### 3. 접속 방식

#### A. USB 포트포워딩 (같은 기기, 터널 불필요)

```bash
adb reverse tcp:7777 tcp:7777
adb shell am start -a android.intent.action.VIEW -d "http://localhost:7777" com.android.chrome
```

#### B. 터널 (USB 없이 Wi-Fi/LTE만으로)

```bash
URL="$(fsh status | grep -oE 'https://[^ ]+' | head -1)"
echo "URL: $URL"
adb shell am start -a android.intent.action.VIEW -d "$URL" com.android.chrome   # 기기가 USB로 붙어 있을 때
adb reverse --remove tcp:7777 2>/dev/null                                        # 포워딩은 필요 없다
```

`fsh mobile`은 adb가 연결돼 있으면 이 과정을 자동으로 합니다(Chrome 자동 열기 + QR).

### 4. 스크린샷

```bash
adb shell screencap -p /sdcard/vt_test.png && adb pull /sdcard/vt_test.png /tmp/vt_mobile.png
```

캡처본은 Read 도구로 확인하세요: `/tmp/vt_mobile.png`

### 5. 동기화 테스트

맥에서 tmux에 명령을 보내고 폰에서 확인합니다. **소켓을 반드시 지정하세요** — FarShell은
격리된 `-L fsh` 소켓을 쓰므로, 맨 `tmux send-keys`는 사용자의 개인 세션을 건드리거나
아무 데도 안 갑니다.

```bash
tmux -L fsh send-keys -t dev "echo 'sync test OK'" Enter
sleep 1
adb shell screencap -p /sdcard/vt_sync.png && adb pull /sdcard/vt_sync.png /tmp/vt_sync.png
```

맥 쪽에서 결과를 직접 읽을 때도 같습니다:

```bash
tmux -L fsh capture-pane -t dev -p -S -5
```

### 6. 화면 제어

```bash
adb shell input keyevent KEYCODE_WAKEUP          # 화면 켜기
adb shell input swipe 540 2000 540 1000 300      # 잠금 해제 스와이프
adb shell input keyevent KEYCODE_SLEEP           # 화면 끄기
```

### 7. 정리

adb 포워딩만 되돌리면 됩니다. 서버·터널은 계속 쓸 거라면 건드리지 마세요.

```bash
adb reverse --remove tcp:7777 2>/dev/null
```

### 체크리스트

- [ ] 웹 UI 로드 (탭 바, 터미널, 키바)
- [ ] tmux 세션 자동 attach
- [ ] 맥 → 폰 실시간 동기화
- [ ] 작업 완료 알림 토스트
- [ ] TTS 재생
- [ ] 마이크 녹음 → STT → tmux 입력
- [ ] 핸즈프리 모드 토글
- [ ] 파일 업로드 (📎)
- [ ] **서버를 내렸을 때 "서버에 연결할 수 없습니다" 배너가 뜨는가** — 죽은 주소에서도
      Service Worker가 캐시 화면을 내주므로, 이게 없으면 고장을 구분할 수 없다
