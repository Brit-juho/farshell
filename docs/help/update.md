# FarShell 업데이트

```bash
fsh update                      # 저장된 채널로 업데이트
fsh update --channel stable     # 서명된 최신 릴리스(vX.Y.Z)
fsh update --channel dev        # origin/dev 최신
fsh update --channel devmode    # 자동 업데이트 끔 (직접 git pull)
fsh update --to v2.5.0          # 특정 릴리스로 (서명된 태그만)
fsh update --check              # 바꾸지 않고 비교만
fsh update --restart            # 끝나면 fsh restart 까지
fsh update --rollback           # 직전 커밋으로
fsh update trust                # 릴리스 서명 키 고정 (처음 한 번)
fsh sync [--dry-run]            # 훅·스킬만 현재 코드에 맞춤
fsh update status               # 서버가 마지막으로 확인한 결과 (네트워크 요청 없음)
fsh update auto on|off|status   # 자동 업데이트 (기본 꺼짐)
```

## 채널

| 채널 | 받는 것 | 검증 |
|---|---|---|
| stable | 최신 `vX.Y.Z` 태그 (분리된 HEAD) | 태그 서명 + `origin/master` 이력 |
| dev | `origin/dev` 최신, 빨리 감기만 | 없음 (GitHub 계정 보안에 의존) |
| devmode | 아무것도 안 함 | — |

채널은 `--channel` 로 정하면 `~/.vt/update-channel` 에 저장됩니다. 채널이 없고 지금
브랜치가 `master`도 아니면 `fsh update` 는 움직이지 않고 채널을 정하라고 알립니다.
개발 머신은 `devmode`(직접 pull) 또는 `dev` 를 쓰세요.

## 서명 검증 (stable)

stable 은 태그에 붙은 SSH 서명이 **내가 신뢰하기로 한 키**와 맞을 때만 받습니다.
신뢰 키는 리포 밖 `~/.vt/update-signers` 에만 둡니다 — 리포가 침해돼도 공격자가
신뢰 키를 바꾸지 못합니다.

1. 처음 한 번 `fsh update trust` — 최신 서명 태그의 키 지문을 보여 주고 확인을 받습니다.
   릴리스에 서명하는 본인 키의 지문(`ssh-keygen -lf <키>.pub`)과 같은지 직접 대조하세요.
   `--key <공개키 파일>` 로 키를 직접 줄 수도 있습니다. 다른 키를 이미 신뢰 중이면 `--replace`.
2. 서명 없는 태그, 다른 키로 서명한 태그, 옛 서명 태그를 새 이름에 붙인 태그,
   `origin/master` 이력에 없는 태그는 거부합니다.

## 실패와 되돌리기

- 추적 파일에 로컬 변경이 있으면 시작하지 않습니다 (`--check` 는 제외).
- 코드를 옮긴 뒤 의존성 설치·빌드가 실패하면 원래 커밋·브랜치로 되돌립니다.
  이미 설치된 pip/npm 패키지는 되돌리지 않습니다.
- 성공하면 이전 위치를 `~/.vt/update-prev.json` 에 남겨 `fsh update --rollback` 이 씁니다.
- 서버는 자동으로 재시작하지 않습니다 (열린 세션이 끊김). `fsh restart` 또는 `--restart`.

## 훅·스킬 동기화

업데이트가 끝나면 Claude Code·Codex 훅(`fsh hooks install` 과 같은 등록기)과 전역
`fsh` 스킬 링크를 현재 코드에 맞춥니다. 직접 pull 했다면 `fsh sync`. 링크가 아닌 사본이
있는 스킬은 덮어쓰지 않고 알립니다.

## 새 버전 확인 (자동, 읽기 전용)

서버가 뜨고 몇 분 뒤, 그리고 24시간마다 `origin` 만 확인합니다 (`git fetch` 하나 — 토큰·사용자
정보는 보내지 않습니다). 결과는 `fsh status` / `fsh doctor` 의 「업데이트」 줄, FarShell 메뉴의
「업데이트 있음 (vX.Y.Z)」 줄, 설정 > 업데이트에 보입니다. 확인만 하고 아무것도 바꾸지 않습니다.

- 끄기: 설정 > 업데이트 > 「새 버전 확인」 OFF (비밀번호 재확인), 또는 환경변수 `VT_UPDATE_CHECK=0`.
  끄면 어떤 네트워크 요청도 하지 않습니다.
- stable 은 서명 검증을 통과한 최신 태그만 「있음」으로 보여 줍니다. 서명이 안 맞는 태그는
  이유와 함께 보여 줄 뿐 「업데이트 있음」으로 알리지 않습니다.
- dev 채널은 `origin/dev` 와 현재 커밋을 비교합니다. devmode·채널 미설정 설치는 알림만 하고 적용하지 않습니다.

## 자동 업데이트 (기본 꺼짐)

```bash
fsh update auto on              # 위험을 설명하고 확인을 받은 뒤 켬 (비대화형은 --yes)
fsh update auto on --hour 3     # 적용 시각 (지역 시간 0~23, 기본 04시)
fsh update auto off
fsh update auto status
```

켜면 FarShell 이 **네트워크에서 받은 코드로 스스로 바뀌고 서버를 다시 시작**합니다. 그래서 아래를 전부 만족할 때만 합니다.

| 조건 | 아니면 |
|---|---|
| 채널이 `stable` 로 저장돼 있다 | 알림만 (dev·devmode·미설정은 절대 자동 적용 안 함) |
| 태그 서명이 신뢰 키와 맞고 `origin/master` 이력에 있다 | 거부하고 이유를 남김 |
| 신뢰 키를 사람이 `fsh update trust` 로 고정했다 | 거부 (자동으로 신뢰하지 않음) |
| 메이저 버전이 오르지 않는다 (2.x → 3.0.0 은 직접) | 거부하고 알림 |
| 추적 파일에 로컬 변경이 없다 | 거부하고 알림 |
| 정해 둔 시각(1시간) 안이다 | 다음 점검으로 미룸 |
| 최근 15분 터미널 입력이 없고 작업 중·입력 대기 에이전트가 없다 | 미룸 (7일 넘게 못 하면 멈추고 알림) |

- 같은 버전의 적용이 3번 실패하면 멈추고 알립니다.
- 적용은 서버와 분리된 프로세스가 `fsh update` 와 같은 경로로 합니다 — 의존성은 `--require-hashes` 잠금대로,
  빌드가 실패하면 원래 커밋으로 되돌립니다. 동시에 하나만 돌고(수동 `fsh update` 와도), 직전 위치는
  `~/.vt/update-prev.json` 에 남아 `fsh update --rollback` 으로 되돌릴 수 있습니다.
- 성공하면 서버가 열린 화면들에 먼저 알린 뒤 `fsh restart` 합니다. 화면은 잠시 끊겼다 다시 붙고,
  **tmux 세션은 서버가 아니라 tmux 가 쥐고 있어 그대로 유지**됩니다. 결과는 메뉴/토스트로 한 번 알립니다.
- 켜고 끄는 일은 웹 비밀번호 재확인(승격)이 필요하고 감사 기록에 남습니다. 에이전트·peer·기계 토큰은 못 바꿉니다.
- 신뢰하는 만큼 릴리스 계정·서명 키가 침해되면 영향을 받습니다. 신경 쓰이면 꺼 두고 `fsh update` 를 직접 실행하세요.
