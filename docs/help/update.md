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
