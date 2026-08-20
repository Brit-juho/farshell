---
name: fsh-mobile
description: |
  모바일 기기에서 FarShell 테스트. adb 포트포워딩, Chrome 열기, 스크린샷 캡처,
  원격 터널 접속까지 처리. Use when asked to "모바일 테스트", "폰에서 테스트",
  "mobile test", "adb 테스트", "모바일로 확인".
allowed-tools:
  - Bash
  - Read
  - AskUserQuestion
---

## 모바일 테스트 스킬

### 사전 조건

- 서버가 실행 중이어야 합니다 (아니면 `/fsh-start` 먼저 실행)
- USB 디버깅이 활성화된 Android 기기가 연결되어 있어야 합니다

### 1. 기기 확인

```bash
adb devices 2>&1
```

기기가 없으면 사용자에게 USB 연결 + USB 디버깅 활성화를 안내하세요.

### 2. 접속 방식 선택

#### A. 로컬 네트워크 (USB 연결 필요)

```bash
# 포트 포워딩
adb reverse tcp:7777 tcp:7777

# Chrome에서 열기
adb shell am start -a android.intent.action.VIEW -d "http://localhost:7777" com.android.chrome
```

#### B. 원격 (USB 불필요, Wi-Fi만 있으면 됨)

Cloudflare Tunnel이 실행 중이어야 합니다:
```bash
TUNNEL_URL=$(grep -o 'https://[^ ]*trycloudflare.com' /tmp/cloudflared.log | head -1)
echo "Tunnel: $TUNNEL_URL"

# 기기에서 열기 (USB 연결 상태일 때)
adb shell am start -a android.intent.action.VIEW -d "$TUNNEL_URL" com.android.chrome
```

원격 모드에서는 포트 포워딩이 필요 없습니다:
```bash
adb reverse --remove tcp:7777 2>/dev/null
```

### 3. 스크린샷 캡처

```bash
adb shell screencap -p /sdcard/vt_test.png && adb pull /sdcard/vt_test.png /tmp/vt_mobile.png
```

캡처한 이미지는 Read 도구로 확인하세요: `/tmp/vt_mobile.png`

### 4. 동기화 테스트

맥북에서 tmux에 명령을 보내고 모바일에서 확인:

```bash
# 맥북에서 명령 전송
tmux send-keys -t dev "echo 'sync test OK'" Enter

# 1초 후 모바일 스크린샷
adb shell screencap -p /sdcard/vt_sync.png && adb pull /sdcard/vt_sync.png /tmp/vt_sync.png
```

### 5. 음성 입력 테스트

모바일에서 마이크 버튼(🎤)을 탭하고 말하면 STT → tmux에 입력됩니다.
tmux에서 결과 확인:

```bash
tmux capture-pane -t dev -p -S -5
```

### 6. 화면 제어 (잠금 해제 등)

```bash
# 화면 켜기
adb shell input keyevent KEYCODE_WAKEUP

# 잠금 해제 (스와이프)
adb shell input swipe 540 2000 540 1000 300

# 화면 끄기
adb shell input keyevent KEYCODE_SLEEP
```

### 체크리스트

테스트 후 다음 항목을 확인하세요:

- [ ] 웹 UI 로드 (탭 바, 터미널, 보이스바)
- [ ] tmux 세션 자동 attach
- [ ] CLI → 모바일 실시간 동기화
- [ ] 작업 완료 알림 토스트
- [ ] TTS "터치하여 재생" 버튼
- [ ] 마이크 녹음 → STT → tmux 입력
- [ ] 핸즈프리 모드 토글
- [ ] 파일 업로드 (📎)
