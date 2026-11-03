# 보안 정책

[English](./SECURITY.md)

FarShell은 셸을 네트워크에 올리는 도구라서 보안 제보를 진지하게 받습니다.

## 취약점 제보

**취약점은 공개 이슈로 올리지 마세요.** GitHub의 비공개 제보를 쓰세요: 이 저장소의 **Security** 탭 →
**Report a vulnerability**. 버전(`cat VERSION`), 한 일, 일어난 일, 기대한 동작을 적어 주세요. 본인 맥에서 만든
재현은 환영하지만 남의 서버에는 시험하지 마세요.

관리자가 가능한 한 빨리 답하며 보통 며칠 안에 합니다. 한 사람이 운영하는 프로젝트라 현상금이나 수정 기한 보장은
없지만, 확인된 제보는 고치고 `CHANGELOG.md`에 (원치 않으면 이름 없이) 남깁니다.

## 지원 버전

최신 릴리스([`CHANGELOG.md`](./CHANGELOG.md))와 `dev` 브랜치. 옛 버전은 패치하지 않습니다 — 최신
릴리스로 올리세요.

## 무엇을 지키도록 만들었고, 무엇은 아닌가

로그인한 사람은 내 맥 터미널 앞에 앉은 사람과 같은 힘을 갖습니다. FarShell은 **그 밖의 모두**를 막고, 폰을
잃어버리거나 링크가 새어도 할 수 있는 일을 줄이도록 만들었습니다.

- 원격 접속은 비밀번호(scrypt 해시), 새 기기용 2차 인증(`fsh otp setup`, 선택), 기기별 폐기(`fsh device revoke`),
  방문자별 잠금, DNS rebinding·교차 사이트 요청을 막는 Host/Origin 검사 뒤에 있습니다. `fsh start`는 비밀번호 없이는 서버를 띄우지
  않습니다(없으면 만듭니다).
- 공개 입구는 기본이 Cloudflare 퀵 터널입니다. 주소를 아는 누구나 로그인 화면까지는 닿습니다.
  `VT_TUNNEL_ALLOWED_MAIL`이 그 앞에 이메일 PIN을 세우고, `fsh start --network tailscale`은 서버를 공개 인터넷에서
  아예 뺍니다.
- 코드 뷰어는 읽기 전용이고 지정한 폴더 안으로 제한되며 거부 목록이 있습니다. 공유 링크는 서명·만료·폐기가 되고,
  노출한 포트에는 별도 접근 모드가 있습니다.

**막지 못하는 것**:

- **같은 macOS 사용자로 도는 다른 프로세스.** `~/.vt`, `~/.vt.env`, tmux 세션을 읽을 수 있습니다. 직접 띄운 에이전트도
  내 계정으로 돕니다. 권한 엔진은 FarShell을 거치는 에이전트의 행동을 기록하고 제한하지만, 이미 내 계정을 가진
  프로그램은 막지 못합니다(`docs/ref/auth.md`의 "same-uid").
- 이미 디스크를 읽을 수 있는 사람, 등록한 기기의 침해된 브라우저.
- Cloudflare 퀵 터널의 TLS가 그쪽 엣지에서 끝난다는 점. `--e2e`는 터미널 스트림에 암호화 계층을 한 겹 더합니다.

## 강화 체크리스트

1. 강한 비밀번호(`fsh password`)와 `fsh otp setup`.
2. 공개 인터넷이 필요 없으면 Tailscale(`fsh start --network tailscale`).
3. 공개 터널을 쓴다면 `VT_TUNNEL_ALLOWED_MAIL`을 설정하고, `fsh device list` / `fsh device revoke`로 더는 안 쓰는
   기기를 정리.
4. `VT_BROWSE_ROOTS`는 가능한 한 좁게(기본 `~/GitHub`).
5. 변경 뒤 `fsh doctor`, 보안 수정은 최신 릴리스로 올려서 받으세요.

## 내려받은 파일 확인

릴리스 파일은 `.sha256`과 함께 GitHub 릴리스에 붙습니다.

```bash
shasum -a 256 -c farshell-X.Y.Z.tar.gz.sha256
gh attestation verify farshell-X.Y.Z.tar.gz --repo <owner>/farshell   # 빌드 출처 증명(있을 때)
```

증명은 릴리스 워크플로가 만들며, 그 단계가 생기기 전 릴리스에는 없습니다. `install.sh`는 릴리스된 코드만 담는
`master`를 클론합니다.
