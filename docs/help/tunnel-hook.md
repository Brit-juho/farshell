# 터널 URL 자동 게시 (VT_TUNNEL_HOOK)

익명 Cloudflare 터널은 **터널 프로세스를 다시 만들 때 URL이 바뀝니다.**
`fsh restart`는 터널을 유지하므로 서버만 재시작하면 주소가 유지됩니다.

```
https://example-one.trycloudflare.com   ← 오늘
https://example-two.trycloudflare.com         ← 내일
```

그래서 폰이나 다른 기기에서 붙으려면 매번 새 URL을 어딘가로 옮겨 적어야 합니다.
그 "어딘가"는 사람마다 다릅니다 — 개인 Notion 페이지, 나에게 보내는 Slack DM,
ntfy 토픽, 텔레그램 봇, 그냥 iCloud Drive의 텍스트 파일...

**fsh는 특정 서비스를 알지 않습니다.** 대신 URL이 바뀔 때 여러분이 지정한 명령을
한 번 부릅니다. 나머지는 그 명령이 알아서 합니다.

> 고정 주소를 원한다면 훅 대신 **주소가 안 바뀌는 입구**가 답입니다.
> - **명명 터널** (`fsh tunnel setup`) — Cloudflare 계정 + 보유 도메인
> - **ngrok 예약 도메인** — `VT_TUNNEL_PROVIDER=ngrok` + `VT_NGROK_DOMAIN=...`
>
> **ngrok으로 고정 도메인을 쓰면 이 훅은 주소가 실제로 바뀔 때만 발사됩니다**
> 같은 URL을 재사용한 `start`에서는 호출하지 않습니다.
> `fsh tunnel hook`으로 직접 부르는 경우는 언제나 그대로 나갑니다.

---

## 계약

```bash
# ~/.vt.env (홈 디렉토리, gitignored — 여기 적은 건 리포에 안 들어갑니다)
VT_TUNNEL_HOOK='내-스크립트 또는 셸 한 줄'
```

훅이 호출되는 시점: `fsh start`(터널 시작),
`fsh tunnel expose`, `fsh tunnel unexpose`, `fsh stop`(터널 종료),
`fsh tunnel hook`(수동).

| 전달 경로 | 내용 |
|---|---|
| **stdin** | `라벨<TAB>URL` 줄들. 메인 터널이 첫 줄, 이후 `expose`한 포트들 |
| `$VT_TUNNEL_EVENT` | `start` / `expose` / `unexpose` / `stop` / `manual` |
| `$VT_TUNNEL_MAIN_URL` | 메인 터널 URL (없으면 빈 값) |

### `stop` 이벤트

`stop`은 **stdin이 비어서 오는 유일한 이벤트**입니다. "알릴 URL이 없다"가 아니라
**"이제 접속할 곳이 없다"**는 뜻이므로, 게시 대상에 그 사실을 반영하세요.

익명 터널은 내릴 때마다 주소가 죽습니다. 이 이벤트를 처리하지 않으면 게시해 둔
페이지가 **죽은 URL을 계속 가리킵니다** — 보는 사람은 훅이 고장난 건지 그냥 꺼진
건지 구분할 수 없습니다.

```bash
# 훅 안에서 이렇게 갈라 쓰면 됩니다
if [ "$VT_TUNNEL_EVENT" = "stop" ]; then
  echo "접속 종료됨" > ~/Documents/fsh-urls.txt
else
  cat > ~/Documents/fsh-urls.txt
fi
```

터널이 **떠 있었을 때만** 쏩니다. 이미 꺼진 상태에서 `fsh stop`을 다시 쳐도
훅은 호출되지 않습니다.

stdin 예시:

```
터미널 (VT)	https://example-one.trycloudflare.com
개발 앱	https://example-two.trycloudflare.com
```

**훅이 실패해도 터널은 정상 동작합니다.** 종료 코드가 0이 아니면 경고만 찍고 넘어갑니다.
미설정이면 아무 일도 일어나지 않습니다.

설정한 훅을 지금 바로 시험해 보려면:

```bash
fsh tunnel hook      # 전달될 내용을 보여주고 실제로 한 번 실행
```

---

## 방식별 예시

아래는 **제안**입니다. 그대로 쓰기보다 각자 쓰는 도구에 맞춰 고쳐 쓰세요.

### 1. 파일에 적기 (가장 단순, 의존성 0)

```bash
VT_TUNNEL_HOOK='cat > ~/Documents/fsh-urls.txt'
```

iCloud/Dropbox 폴더에 두면 폰에서 그대로 열립니다.

### 2. ntfy 푸시 (계정 불필요)

```bash
VT_TUNNEL_HOOK='curl -s -T - -H "Title: VT URL" https://ntfy.sh/my-secret-topic'
```

폰에 ntfy 앱을 깔고 같은 토픽을 구독해 두면 URL이 바뀔 때마다 알림이 옵니다.
`fsh` 자체 알림(`VT_NOTIFY_URL`)과 같은 서비스지만 용도가 다르니 토픽은 나눠 쓰세요.

> 토픽 이름이 곧 비밀번호입니다. 추측 가능한 이름은 피하세요.

### 3. Slack (개인 DM / 채널)

Incoming Webhook을 만든 뒤:

```bash
VT_TUNNEL_HOOK='jq -Rs "{text: .}" | curl -s -X POST -H "Content-Type: application/json" -d @- "$SLACK_WEBHOOK_URL"'
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
```

### 4. 텔레그램 봇

```bash
VT_TUNNEL_HOOK='curl -s -X POST "https://api.telegram.org/bot$TG_TOKEN/sendMessage" --data-urlencode "chat_id=$TG_CHAT" --data-urlencode "text=$(cat)"'
```

### 5. Notion 페이지

공식 스크립트는 제공하지 않습니다 — Notion API 토큰과 페이지 구조는 개인 설정이라
리포에 넣을 성격이 아니기 때문입니다. 직접 만든다면 요령은 이렇습니다:

1. https://www.notion.so/profile/integrations 에서 Internal integration 생성 → 시크릿 확보
2. 게시할 페이지에서 `•••` → **연결(Connections)** 에 그 integration 추가
   (이걸 빼먹으면 API가 404를 냅니다 — 가장 흔한 실수)
3. 훅 스크립트에서 페이지의 최상위 블록을 훑어 **표식이 있는 블록 하나를 찾아 PATCH**로
   덮어씁니다. 없으면 새로 append.
   - 매번 append만 하면 터널을 켤 때마다 블록이 쌓입니다. 찾아서 갱신하는 게 핵심.
   - 블록을 실수로 지워도 다음 실행 때 다시 만들어져 자가복구됩니다.
4. 토큰은 `~/.vt.env`에 두고(`chmod 600`), 스크립트는 `~/.config/vt/hooks/` 처럼
   **리포 밖**에 두세요.

```bash
VT_TUNNEL_HOOK='"$VT_PYTHON" ~/.config/vt/hooks/notion_publish.py'
```

### 6. 아무 것도 안 하기

기본값입니다. `fsh status`나 `fsh tunnel list`로 그때그때 확인하고,
`fsh mobile`의 QR 코드로 폰에서 바로 붙는 것으로 충분한 경우가 많습니다.

---

## 주의

- **훅에 들어가는 값은 셸로 실행됩니다.** 신뢰할 수 있는 명령만 넣으세요.
- **`~/.vt.env`는 실행하지 않고 파싱합니다.** 저장소의 `lib/vt_env.sh`로 값을
  저장하세요. `VT_TUNNEL_HOOK`에 저장한 명령은 훅을 호출할 때 별도로 실행됩니다.
- 메인 웹 UI는 `fsh password` 또는 `VT_AUTH_TOKEN`으로 인증을 설정하세요.
  추가 포트는 공유 모드에 따라 접근 권한이 다릅니다(`fsh help tunnel`).
  기기 등록 티켓이 포함된 `fsh mobile` URL은 게시하지 마세요.
- 훅은 터널 시작 흐름 안에서 **동기적으로** 실행됩니다. 오래 걸리는 작업은
  스크립트 쪽에서 백그라운드로 넘기세요.
