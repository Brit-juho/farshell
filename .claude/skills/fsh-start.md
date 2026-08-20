---
name: fsh-start
description: |
  FarShell 서버 시작 + Cloudflare Tunnel 원격 접속 설정.
  서버 실행, tmux 세션 준비, 터널 URL 생성까지 원스텝으로 처리.
  Use when asked to "서버 시작", "voice terminal 시작", "start fsh", "start vt", "원격 접속 설정".
allowed-tools:
  - Bash
  - Read
---

## FarShell 서버 시작 스킬

이 스킬은 FarShell 서버를 시작하고 원격 접속을 설정합니다.

### 실행 순서

1. **기존 프로세스 확인 및 정리**

```bash
# 이미 실행 중인 서버 확인
lsof -i :7777 -t 2>/dev/null && echo "SERVER_ALREADY_RUNNING" || echo "SERVER_NOT_RUNNING"
```

이미 실행 중이면 재시작 여부를 사용자에게 확인하세요.

2. **서버 시작**

```bash
cd "$CLAUDE_PROJECT_DIR/server"
nohup /opt/homebrew/Caskroom/miniforge/base/envs/whisper/bin/python -m uvicorn main:app --host 0.0.0.0 --port 7777 > /tmp/vt-server.log 2>&1 &
echo "SERVER_PID: $!"
```

3초 후 헬스체크:
```bash
curl -sf http://localhost:7777/ -o /dev/null && echo "SERVER_OK" || echo "SERVER_FAIL"
```

실패 시 로그 확인: `tail -20 /tmp/vt-server.log`

3. **tmux 세션 준비**

```bash
# tmux 세션이 없으면 기본 세션 생성
tmux list-sessions 2>/dev/null || tmux new-session -d -s dev
```

4. **Cloudflare Tunnel (원격 접속)**

사용자에게 원격 접속이 필요한지 확인 후 실행:

```bash
nohup cloudflared tunnel --url http://localhost:7777 > /tmp/cloudflared.log 2>&1 &
echo "TUNNEL_PID: $!"
```

URL 추출:
```bash
grep -o 'https://[^ ]*trycloudflare.com' /tmp/cloudflared.log | head -1
```

5. **접속 정보 출력**

| 환경 | URL |
|------|-----|
| 데스크톱 | http://localhost:7777 |
| 같은 네트워크 | http://$(ipconfig getifaddr en0):7777 |
| 원격 (어디서든) | {터널 URL} |

### 종료

```bash
# 서버 종료
lsof -i :7777 -t | xargs kill 2>/dev/null

# 터널 종료
pkill -f "cloudflared tunnel" 2>/dev/null

# tmux는 유지 (필요시 tmux kill-server)
```
