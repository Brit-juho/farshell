"""I4 — CHANGELOG → 릴리스 노트 추출기.

릴리스 노트를 손으로 다시 쓰면 CHANGELOG와 두 벌이 되고 하나는 반드시 낡는다
(I3가 막으려는 것과 같은 드리프트). 그래서 CHANGELOG가 단일 진실이고 노트는
파생물이다 — 그 추출이 정확한지, 그리고 **못 찾았을 때 빈 노트를 만들지 않는지**
가 검사 대상이다.
"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SPEC = importlib.util.spec_from_file_location("release_notes", ROOT / "scripts" / "release_notes.py")
release_notes = importlib.util.module_from_spec(SPEC)
sys.modules["release_notes"] = release_notes
SPEC.loader.exec_module(release_notes)

SAMPLE = """# Changelog

## [Unreleased]

### Added
- 아직 안 나간 것

## [1.7.0] — 2026-08-04

### Security
- 기기 화이트리스트
- OTP 관문

## [1.6.0] — 2026-07-12

### Added
- 웹 로그인 비밀번호
"""


def test_extracts_only_that_version():
    heading, body = release_notes.extract("1.7.0", SAMPLE)
    assert "1.7.0" in heading
    assert "기기 화이트리스트" in body
    assert "웹 로그인 비밀번호" not in body, "다음 버전 절이 섞이면 안 된다"
    assert "아직 안 나간 것" not in body, "Unreleased가 섞이면 안 된다"


def test_extracts_last_section_to_end():
    _, body = release_notes.extract("1.6.0", SAMPLE)
    assert "웹 로그인 비밀번호" in body


def test_missing_version_returns_none():
    """빈 노트를 만드느니 실패한다 — 릴리스에 '내용 없음'이 붙는 게 더 나쁘다."""
    assert release_notes.extract("9.9.9", SAMPLE) is None


def test_real_changelog_has_current_version():
    """VERSION이 가리키는 절이 실제로 있어야 릴리스 워크플로가 돈다."""
    version = (ROOT / "VERSION").read_text().strip()
    found = release_notes.extract(version, (ROOT / "CHANGELOG.md").read_text())
    assert found is not None, f"CHANGELOG.md에 [{version}] 절이 없다 — 릴리스가 실패한다"
    assert found[1].strip(), "절이 비어 있다"
