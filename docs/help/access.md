# fsh help access — 접속 범위와 보안

fsh를 어떻게 열지(누가 접속할 수 있는지)는 **`fsh start`가 정하고**, 폰으로 접속할 주소와 QR은
**`fsh mobile`이 보여준다**. 지금 상태는 `fsh status`의 「접속 보안」 블록에서 한눈에 본다.

## 시작: `fsh start`

```bash
fsh start                              # 기본: 공개 터널 + 인증 (all)
fsh start --network tailscale          # 같은 tailnet 안에서만 (터널 없음)
fsh start --network lan                # 같은 네트워크에서만 (터널 없음)
fsh start --network localhost          # 이 맥에서만 (터널 없음)
fsh start --safe                       # 위험 명령 차단 모드
fsh start --voice                      # Voice Daemon도 함께
```

| 접근 범위 | 누가 들어올 수 있나 | 터널 |
|---|---|---|
| `all` (기본) | 인터넷 어디서나. 대신 **인증이 필수** | Cloudflare/ngrok |
| `tailscale` | 같은 tailnet 기기만 | 없음 |
| `lan` | 같은 네트워크 기기만 | 없음 |
| `localhost` | 이 맥만 | 없음 |

- **어느 범위든 인증 없이는 서버를 띄우지 않는다.** 인증 없는 서버는 닿는 누구나(같은 망·tailnet·공개 주소,
  또는 이 맥에서 연 악성 웹페이지)에게 셸이다. 비밀번호가 없으면 `fsh start`·`voice`·`clip`이 **무작위 웹 로그인
  비밀번호를 만들어 터미널에 한 번만 보여 주고** 해시만 저장한다. 바꾸려면 `fsh password`.
  터미널이 아닌 곳(스크립트·자동 실행)에서는 만들지 않고 멈춘다 — 먼저 `fsh password`를 실행한다.
  비밀번호를 만들 수 없을 때 위험을 알고도 열려면 `fsh start --force`.
- 이미 서버가 떠 있으면 `--network`/`--safe`는 **다음 재시작부터** 적용된다:
  `fsh restart --network tailscale`.
- `fsh restart`는 직전에 정한 접근 범위를 그대로 이어간다. `fsh stop`(전체 종료)을 하면 기록이 지워진다.
- 범위를 `all`에서 좁혀도 이미 떠 있는 터널 프로세스는 남는다. 끄려면 `fsh stop` 후 다시 시작한다.

## 폰으로 접속: `fsh mobile`

```bash
fsh mobile                    # 접속 URL + QR (서버가 떠 있어야 함)
fsh mobile --e2e              # E2E 암호화로 열기 (터널이 평문을 보지 못함)
fsh mobile --session dev      # 폰이 열릴 때 tmux 세션 'dev'를 바로 보기
fsh mobile --temp             # 이 QR로 등록되는 기기는 1일 뒤 만료 (남의 폰에 잠깐 열어 줄 때)
```

- URL에는 **5분짜리 1회용 등록 티켓**이 붙는다. 이 URL을 연 기기가 등록된다 — 남에게 보내지 않는다.
- `fsh handoff mobile`은 `fsh mobile --session <현재 세션>`과 같다(서버가 없으면 먼저 시작).
- 옛 옵션(`--network`/`--safe`/`--force`)을 `mobile`에 주면 `fsh start`를 대신 실행한 뒤 QR을 보여준다.

## 상태 보기: `fsh status`의 「접속 보안」

| 항목 | 의미 |
|---|---|
| 접근 범위 | 위 표의 값. `VT_ACCESS_SPEC`으로 허용 IP 목록을 직접 지정했다면 함께 표시 |
| 외부 주소 | 고정 도메인(명명 터널/ngrok 예약 도메인)인지, 재시작하면 바뀌는 익명 터널인지 |
| 인증 | 비밀번호 / 기계 토큰. `all`인데 인증이 없으면 경고 |
| OTP | 켜면 새 기기 등록 때 OTP를 한 번 더 요구한다 (`fsh otp setup`) |
| 등록 기기 | 로그인된 기기 수 (`fsh device list`, 분실 시 `fsh device revoke <id>`) |

## 자주 쓰는 관리 명령

```bash
fsh password [clear]          # 웹 로그인 비밀번호 설정/해제
fsh otp [status|setup|disable]
fsh device [list|rename|revoke|prune]
fsh tunnel list               # 열려 있는 터널 전부
fsh doctor                    # 설치·환경·인증 점검
```

고정 도메인이 필요하면 명명 터널(`fsh tunnel setup`) 또는 ngrok 예약 도메인을 쓴다.
주소가 바뀔 때 알림을 받으려면 `fsh help tunnel-hook`.
