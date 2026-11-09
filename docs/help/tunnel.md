# fsh help tunnel — 터널 관리

외부에서 접속하는 입구(Cloudflare 또는 ngrok 터널)를 관리한다. 평소엔 `fsh start`가 알아서 띄우고,
이 명령들은 상태 확인과 예외 상황, 다른 로컬 앱 공개에 쓴다. 접근 범위와 인증은 `fsh help access`.

```bash
fsh tunnel list                      # 열려 있는 터널 전부 (메인 + 추가 포트)
fsh tunnel status                    # 터널 제공자·모드(익명/명명)·실행 상태
fsh tunnel restart                   # 좀비 재연결(응답 없음) 상태여도 강제로 새 터널 기동
fsh tunnel watchdog                  # 좀비 재연결 자동 감지 데몬 상태 확인/시작
                                     #   (fsh start 시 자동 기동, 감지하면 자동 restart)
fsh tunnel hook                      # URL 변경 훅 확인 + 즉시 실행 (fsh help tunnel-hook)
```

## 고정 도메인 (명명 터널)

익명 터널은 터널 프로세스를 다시 띄우면 주소가 바뀐다. 주소를 고정하려면 명명 터널이 필요하다
(Cloudflare 계정 + 보유 도메인) — ngrok 예약 도메인(`VT_TUNNEL_PROVIDER=ngrok` + `VT_NGROK_DOMAIN`)도 된다.

```bash
fsh tunnel setup                     # 명명 터널 마법사
fsh tunnel switch <anon|named>       # 익명 ↔ 명명 전환
fsh tunnel teardown                  # 명명 터널 제거
```

`fsh restart`는 터널을 건드리지 않아 서버만 재기동해도 주소가 그대로다. 주소를 지키려면
`fsh stop && fsh start` 대신 항상 `fsh restart`를 쓴다.

## 다른 로컬 앱 공개

FarShell이 아니라 **다른 로컬 서버**(예: 3000번 포트)를 별도 터널로 공개한다. 접근 범위가 `all`일 때만 된다.

```bash
fsh tunnel expose 3000 "앱 이름" [--mode public|session|pin] [--ttl 1h|24h|7d|30d] [--force] [--auto-renew|--no-auto-renew]
fsh tunnel unexpose 3000             # 해당 포트 터널·공유 정책 종료
fsh tunnel pin 3000                  # 접속 코드 재발급 (터널·URL 유지)
fsh tunnel mode 3000 <public|session|pin>   # 공유 모드 변경 (터널·URL 유지)
fsh tunnel auto 3000 <on|off>        # 자동 재노출 켜기/끄기 (자격 유지)
```

| 모드 | 의미 |
|---|---|
| `session` (기본) | 로그인한 기기만 |
| `pin` | 접속 코드를 아는 사람만 |
| `public` | URL을 아는 누구나 (인증 없음) |

공개 인터넷에 여는 동작이라 `expose`는 확인을 요구한다. Chrome 원격 디버깅(9222) 같은 원격 제어 포트는 `--force`가 있어야 열린다. 웹 포트 패널에서는 서버가 위험 포트라고 알려 주면 이유를 보여 주는 확인 창이 뜨고, **포트 번호를 그대로 입력해야** 「공개」 버튼이 켜진다(기본 초점은 「취소」, Esc는 취소).
`session` 공유는 공개 주소를 그냥 열면 안내 화면만 뜬다 — `expose` 출력의 「처음 열기」 링크(또는 FarShell 포트 패널)로 시작한다. 기간이 끝난 노출은 1분 안에 터널까지 내려간다.

### 자동 재노출

`expose`는 옵션을 주지 않으면 「기간이 끝나거나 재부팅해도 자동으로 다시 열까요?」라고 묻는다(기본 아니오). 터미널이 아닌 곳(스크립트, 자동 시작)에서는 묻지 않고 끈다. 웹 포트 패널의 공개 대화상자에도 같은 선택이 있고, 열린 터널 행의 「자동 켜기/끄기」로 나중에 바꾼다.

켜 두면 만료 시각에 내리지 않고 `--ttl`만큼 연장한다(접속 코드·로그인 기기 그대로). 재부팅·`fsh stop`·cloudflared 종료로 터널이 사라지면 서버가 1분 안에 다시 연다. 익명 터널은 다시 열 때 **주소가 바뀌므로** 새 주소는 URL 변경 훅으로 받는다. 접근 범위가 `all`이 아니면 다시 열지 않는다. 끝내려면 `fsh tunnel unexpose`.

## 주소가 바뀔 때 알림

터널 URL이 바뀌면 Notion·Slack 등에 자동 게시하는 훅은 `fsh help tunnel-hook`.
