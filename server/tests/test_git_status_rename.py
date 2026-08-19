"""P3 회귀: git status --porcelain=v1 -z 의 rename/copy(R/C) 파싱.

rename/copy 는 NUL 필드가 2개다: "XY new_path\\0old_path\\0". old_path 는
상태 접두사(XY )가 없는 별도 NUL 필드인데, 이걸 그냥 다음 status record로
오인해서 파싱하면 old_path 앞 두 글자를 가짜 status로 삼켜 UI에 존재하지
않는 파일/상태가 나타난다.
"""

import subprocess
from pathlib import Path

import pytest

from routes.files import _collect_status


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "proj"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "test@example.com")
    _git(r, "config", "user.name", "test")

    (r / "old_name.py").write_text("print('hello')\n" * 5)
    (r / "normal.py").write_text("print('unrelated')\n")
    _git(r, "add", "old_name.py", "normal.py")
    _git(r, "commit", "-q", "-m", "init")

    # rename + 살짝 수정 (git이 rename으로 탐지하도록 원본 내용 대부분 유지)
    (r / "old_name.py").rename(r / "new_name.py")
    (r / "new_name.py").write_text("print('hello')\n" * 5 + "print('renamed')\n")
    _git(r, "add", "-A")

    # 수정만 하는 일반 파일도 하나 섞어서 rename record 뒤 파싱이 안 깨지는지 확인
    (r / "normal.py").write_text("print('unrelated')\nprint('changed')\n")

    return r


def test_rename_produces_one_record_with_orig_file(repo):
    result = _collect_status(repo)
    files = result["files"]

    renamed = [f for f in files if f["file"] == "new_name.py"]
    assert len(renamed) == 1, f"rename이 record 1개가 아니라 {len(renamed)}개로 쪼개짐: {files}"
    entry = renamed[0]
    assert entry["status"].startswith("R")
    assert entry.get("orig_file") == "old_name.py"


def test_rename_old_path_is_not_a_fake_status_entry(repo):
    result = _collect_status(repo)
    files = result["files"]
    # 버그가 있었다면 old_name.py(또는 그 일부)가 "file" 값으로 단독 등장했을 것
    assert not any(f["file"] == "old_name.py" for f in files)


def test_normal_file_after_rename_still_parses(repo):
    result = _collect_status(repo)
    files = result["files"]
    normal = [f for f in files if f["file"] == "normal.py"]
    assert len(normal) == 1
    assert normal[0]["status"].strip() != ""
    assert "orig_file" not in normal[0]
