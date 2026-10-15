"""`fsh tunnel list` — 정책 목록(줄바꿈 포함)을 awk -v 로 넘기면 macOS 기본 awk(BWK)가
"newline in string"으로 프로그램 전체를 포기한다(정책 2개 이상이면 행이 안 나온다).
Linux의 awk는 이를 허용해 동작 테스트로는 못 잡으므로, 환경변수로 넘기는 형태를 고정한다."""

from pathlib import Path

FSH = Path(__file__).resolve().parents[2] / "bin" / "fsh"


def _tunnel_list_body() -> str:
    src = FSH.read_text(encoding="utf-8")
    start = src.index("_tunnel_list()")
    end = src.index("\n}\n", start)
    return src[start:end]


def test_modes_are_passed_by_environment_not_awk_v():
    body = _tunnel_list_body()
    assert '-v modes=' not in body, "여러 줄 문자열을 awk -v 로 넘기면 macOS awk가 실패한다"
    assert 'MODES="$modes" awk' in body
    assert 'ENVIRON["MODES"]' in body
