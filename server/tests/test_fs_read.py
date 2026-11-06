"""`/api/fs/file` 2판 계약 — rev·304·409, 구간 읽기(줄 경계, `line_truncated`, 다중 바이트 경계), 인코딩,
경로 바꿔치기 거부, 옛 응답 모양 유지(fs_read.py, API.md)."""

import os
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import fs_read
import fsguard
import main
from routes import files as files_routes


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(tmp_path))
    r = tmp_path / "proj"
    r.mkdir()
    return r.resolve()


def _get(client, path, **params):
    headers = params.pop("headers", None)
    return client.get("/api/fs/file", params={"path": str(path), **params}, headers=headers)


# --- 옛 응답 모양 ---------------------------------------------------------------------


def test_legacy_shape_is_kept_and_new_fields_are_added(client, root):
    p = root / "a.py"
    p.write_text("print('a')\n")
    r = _get(client, p)
    assert r.status_code == 200
    d = r.json()
    for k in ("path", "size", "binary", "image", "truncated", "content"):
        assert k in d
    assert d["content"] == "print('a')\n" and d["truncated"] is False and d["binary"] is False
    st = os.stat(p)
    assert d["rev"] == f"{st.st_dev}:{st.st_ino}:{st.st_size}:{st.st_mtime_ns}"
    assert d["encoding"] == "utf-8" and d["encoding_confident"] is True and d["bom"] is False
    assert "offset" not in d and "next_offset" not in d
    assert r.headers["etag"].startswith('W/"')
    assert r.headers["cache-control"] == "private, no-cache"


def test_legacy_truncates_at_max_bytes_without_a_broken_char(client, root, monkeypatch):
    monkeypatch.setattr(fsguard, "MAX_BYTES", 10)
    p = root / "k.txt"
    p.write_bytes("abcdefghi가나".encode())   # 9 + 3 + 3 바이트, 10번째 바이트에서 '가'가 잘린다
    d = _get(client, p).json()
    assert d["truncated"] is True
    assert d["content"] == "abcdefghi"           # 깨진 글자(U+FFFD)를 붙이지 않는다
    assert d["encoding"] == "utf-8" and d["encoding_confident"] is True


def test_legacy_binary_and_image(client, root):
    (root / "b.bin").write_bytes(b"\x00\x01\x02\xff" * 10)
    d = _get(client, root / "b.bin").json()
    assert d["binary"] is True and d["image"] is False and d["content"] == ""
    (root / "i.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
    d = _get(client, root / "i.png").json()
    assert d["image"] is True and d["mime"] == "image/png" and d["too_large"] is False


# --- rev, 304, 409 ----------------------------------------------------------------------


def test_if_none_match_gives_304_and_a_change_gives_200(client, root):
    p = root / "a.txt"
    p.write_text("one\n")
    r1 = _get(client, p)
    etag = r1.headers["etag"]
    r2 = _get(client, p, headers={"If-None-Match": etag})
    assert r2.status_code == 304 and r2.content == b""
    assert r2.headers["etag"] == etag and r2.headers["cache-control"] == "private, no-cache"
    # 강한 형태로 돌아와도(프록시가 W/를 떼는 경우) 일치로 본다
    assert _get(client, p, headers={"If-None-Match": etag[2:]}).status_code == 304
    p.write_text("two\n")
    os.utime(p, ns=(1, 2_000_000_000))
    r3 = _get(client, p, headers={"If-None-Match": etag})
    assert r3.status_code == 200 and r3.json()["content"] == "two\n"
    assert r3.headers["etag"] != etag


def test_etag_differs_by_encoding_and_range(client, root):
    p = root / "a.txt"
    p.write_text("x\n")
    tags = {
        _get(client, p).headers["etag"],
        _get(client, p, encoding="latin-1").headers["etag"],
        _get(client, p, offset=0).headers["etag"],
        _get(client, p, offset=0, length=1).headers["etag"],
        _get(client, p, tail=10).headers["etag"],
    }
    assert len(tags) == 5


def test_expected_rev_mismatch_is_409_with_the_current_rev(client, root):
    p = root / "a.txt"
    p.write_text("x\n")
    rev = _get(client, p).json()["rev"]
    assert _get(client, p, offset=0, expected_rev=rev).status_code == 200
    r = _get(client, p, offset=0, expected_rev="1:2:3:4")
    assert r.status_code == 409
    assert r.json()["error"] == "changed" and r.json()["rev"] == rev
    assert r.headers["cache-control"] == "no-store"


def _flapping_rev(monkeypatch, changed_on):
    """읽기 전후의 판 번호를 흉내 낸다 — `changed_on(n)`이 참인 호출(1부터)은 다른 rev를 돌려준다."""
    calls = {"n": 0}

    def fake(st):
        calls["n"] += 1
        return f"other-{calls['n']}" if changed_on(calls["n"]) else "1:1:1:1"

    monkeypatch.setattr(fs_read, "rev_of", fake)
    return calls


def test_change_during_read_is_409(root):
    p = root / "a.txt"
    p.write_text("x\n")
    with pytest.MonkeyPatch.context() as mp:
        calls = _flapping_rev(mp, lambda n: n % 2 == 0)     # 읽은 뒤의 rev 마다 다르다
        with pytest.raises(fs_read.Changed):
            fs_read.read(p, offset=0)
        assert calls["n"] == 6           # 기준 rev 가 없으면 세 번까지 다시 읽어 본다
    with pytest.MonkeyPatch.context() as mp:
        calls = _flapping_rev(mp, lambda n: n % 2 == 0)
        with pytest.raises(fs_read.Changed):
            fs_read.read(p, offset=0, expected_rev="1:1:1:1")
        assert calls["n"] == 2           # 기준 rev 가 있으면 바로 409


def test_one_flap_without_expected_rev_is_retried(root, monkeypatch):
    p = root / "a.txt"
    p.write_text("x\n")
    calls = _flapping_rev(monkeypatch, lambda n: n == 2)
    assert fs_read.read(p)["status"] == 200 and calls["n"] == 4


# --- 구간 읽기 --------------------------------------------------------------------------


def test_range_cuts_at_the_last_line_end_and_chains(client, root):
    p = root / "l.txt"
    p.write_bytes(b"aaa\nbbb\nccc\nddd")
    d = _get(client, p, offset=0, length=10).json()
    assert d["content"] == "aaa\nbbb\n" and d["offset"] == 0 and d["length"] == 8
    assert d["next_offset"] == 8 and d["eof"] is False and d["truncated"] is True
    assert d["line_truncated"] is False
    d2 = _get(client, p, offset=8, length=10, expected_rev=d["rev"]).json()
    assert d2["content"] == "ccc\nddd" and d2["eof"] is True and d2["next_offset"] is None


def test_range_past_eof_is_clipped_and_offset_at_eof_is_empty(client, root):
    p = root / "l.txt"
    p.write_bytes(b"abc\n")
    d = _get(client, p, offset=2, length=1000).json()
    assert d["content"] == "c\n" and d["eof"] is True
    d = _get(client, p, offset=4).json()
    assert d["content"] == "" and d["eof"] is True and d["length"] == 0


@pytest.mark.parametrize("params", [
    {"offset": "-1"}, {"offset": "x"}, {"length": "0"}, {"length": str(fs_read.MAX_RANGE_BYTES + 1)},
    {"tail": "0"}, {"tail": "5", "offset": "0"}, {"offset": "99"}, {"expected_rev": "x" * 200},
])
def test_bad_range_values_are_400(client, root, params):
    p = root / "l.txt"
    p.write_bytes(b"abc\n")
    r = _get(client, p, **params)
    assert r.status_code == 400 and r.json()["error"] == "bad_range"


def test_length_bounds_are_inclusive(client, root):
    p = root / "l.txt"
    p.write_bytes(b"a\n" * 10)
    assert _get(client, p, offset=0, length=1).status_code == 200
    assert _get(client, p, offset=0, length=fs_read.MAX_RANGE_BYTES).status_code == 200


def test_long_line_is_cut_and_flagged_line_truncated(client, root):
    p = root / "long.txt"
    p.write_bytes(b"x" * 50 + b"\nend\n")
    d = _get(client, p, offset=0, length=20).json()
    assert d["line_truncated"] is True and d["content"] == "x" * 20 and d["next_offset"] == 20
    d = _get(client, p, offset=20, length=32).json()     # 다음 구간이 같은 줄을 이어 받는다
    assert d["content"] == "x" * 30 + "\n" and d["line_truncated"] is False


def test_line_truncated_at_the_one_megabyte_cap(client, root):
    p = root / "huge.txt"
    p.write_bytes(b"y" * (fs_read.MAX_RANGE_BYTES + 100) + b"\n")
    d = _get(client, p, offset=0, length=fs_read.MAX_RANGE_BYTES).json()
    assert d["line_truncated"] is True and d["length"] == fs_read.MAX_RANGE_BYTES


def test_utf8_multibyte_never_split_in_a_long_line(client, root):
    p = root / "k.txt"
    p.write_bytes(("가" * 10 + "\n").encode())          # 한 글자 3바이트, 줄 끝이 창 밖
    for length in (1, 2):
        r = _get(client, p, offset=0, length=length)
        assert r.status_code == 400          # 글자 하나보다 짧은 창 — 진척 없는 응답 대신 거절
    d = _get(client, p, offset=0, length=7).json()
    assert d["content"] == "가가" and d["length"] == 6 and d["next_offset"] == 6
    assert d["encoding"] == "utf-8" and "�" not in d["content"]


@pytest.mark.parametrize("enc,codec", [("cp949", "cp949"), ("euc-kr", "euc_kr"), ("shift_jis", "shift_jis")])
def test_dbcs_encodings_cut_at_line_end_and_mid_line(client, root, enc, codec):
    text = ("한글" if enc != "shift_jis" else "日本") * 3
    p = root / "d.txt"
    p.write_bytes((text + "\n" + text + "\n").encode(codec))
    one = len((text + "\n").encode(codec))
    d = _get(client, p, offset=0, length=one + 3, encoding=enc).json()
    assert d["content"] == text + "\n" and d["next_offset"] == one
    d = _get(client, p, offset=0, length=5, encoding=enc).json()      # 2바이트 글자 두 개 + 반 글자
    assert d["line_truncated"] is True and d["length"] == 4
    assert d["content"] == text[:2] and d["encoding_confident"] is True


def test_utf16_range_and_odd_offset(client, root):
    p = root / "w.txt"
    p.write_bytes(codecs_bom_le() + "ab\ncd\n".encode("utf-16-le"))
    d = _get(client, p, offset=0, length=9).json()
    assert d["encoding"] == "utf-16le" and d["bom"] is True and d["encoding_confident"] is True
    assert d["content"] == "ab\n" and d["next_offset"] == 8
    d = _get(client, p, offset=8, encoding="utf-16le").json()
    assert d["content"] == "cd\n" and d["eof"] is True
    r = _get(client, p, offset=3, encoding="utf-16le")
    assert r.status_code == 400 and r.json()["error"] == "bad_range"


def test_utf16_newline_search_ignores_odd_positions(root):
    # `\n\x00`가 홀수 자리에 있는 것은 줄 끝이 아니다: U+0A00(LE 00 0A) 다음 NUL 시작 글자
    data = "਀Ā".encode("utf-16-le")       # 00 0a 00 01 — 1번 자리에 0a 00
    assert fs_read._rfind_line_end(data, "utf-16le") == -1


def test_tail_starts_at_a_line_boundary(client, root):
    p = root / "log.txt"
    p.write_bytes(b"line1\nline2\nline3\n")
    d = _get(client, p, tail=8).json()     # 끝 8바이트: "2\nline3\n" → 줄 경계에서 "line3\n"
    assert d["content"] == "line3\n" and d["offset"] == 12 and d["tail"] is True
    assert d["eof"] is True and d["line_truncated"] is False
    d = _get(client, p, tail=1000).json()
    assert d["content"] == "line1\nline2\nline3\n" and d["offset"] == 0


def test_tail_inside_one_long_utf8_line_skips_continuation_bytes(client, root):
    p = root / "k.txt"
    p.write_bytes("가나다라".encode())
    d = _get(client, p, tail=5).json()     # 끝 5바이트 = '다'의 뒤 2바이트 + '라'
    assert d["line_truncated"] is True and "�" not in d["content"]
    assert d["content"] == "라"


# --- 인코딩 ------------------------------------------------------------------------------


def codecs_bom_le():
    import codecs
    return codecs.BOM_UTF16_LE


def test_utf8_bom_is_detected_and_stripped(client, root):
    p = root / "b.txt"
    p.write_bytes(b"\xef\xbb\xbfhello\n")
    d = _get(client, p).json()
    assert d["content"] == "hello\n" and d["encoding"] == "utf-8" and d["bom"] is True


def test_utf16_without_bom_is_guessed_not_binary(client, root):
    p = root / "w.txt"
    p.write_bytes("hello world\n".encode("utf-16-be"))
    d = _get(client, p).json()
    assert d["binary"] is False and d["content"] == "hello world\n"
    assert d["encoding"] == "utf-16be" and d["encoding_confident"] is False
    assert d["suggested"] == "utf-16be" and d["encoding_reason"] == "guessed_utf16"


def test_unknown_encoding_falls_back_to_utf8_replace_with_a_reason(client, root):
    p = root / "c.txt"
    p.write_bytes("한글 텍스트\n".encode("cp949"))
    d = _get(client, p).json()
    assert d["encoding"] == "unknown" and d["encoding_confident"] is False
    assert d["encoding_reason"] == "not_utf8" and "�" in d["content"] and d["binary"] is False
    d = _get(client, p, encoding="cp949").json()
    assert d["content"] == "한글 텍스트\n" and d["encoding"] == "cp949" and d["encoding_confident"] is True


def test_explicit_encoding_with_invalid_bytes_is_not_confident(client, root):
    p = root / "c.txt"
    p.write_bytes(b"ok \xff\xfe\n")
    d = _get(client, p, encoding="utf-8").json()
    assert d["encoding"] == "utf-8" and d["encoding_confident"] is False
    assert d["encoding_reason"] == "decode_errors"
    d = _get(client, p, encoding="latin-1").json()
    assert d["content"] == "ok \xff\xfe\n" and d["encoding_confident"] is True


def test_unsupported_encoding_is_400(client, root):
    p = root / "a.txt"
    p.write_text("x")
    r = _get(client, p, encoding="ebcdic")
    assert r.status_code == 400 and r.json()["error"] == "bad_encoding"
    assert "cp949" in r.json()["supported"]


def test_binary_with_nul_stays_binary_under_auto(client, root):
    p = root / "x.bin"
    p.write_bytes(bytes(range(256)) * 4)
    d = _get(client, p, offset=0).json()
    assert d["binary"] is True and d["content"] == ""


# --- 경계와 바꿔치기 ---------------------------------------------------------------------


def test_denied_and_outside_paths_stay_403(client, root):
    (root / ".env").write_text("S=1\n")
    assert _get(client, root / ".env", offset=0).status_code == 403
    assert client.get("/api/fs/file", params={"path": "/etc/hosts", "tail": "10"}).status_code == 403


def test_directory_and_fifo_are_404(client, root):
    (root / "d").mkdir()
    assert _get(client, root / "d").status_code == 404
    os.mkfifo(root / "f.txt")
    assert _get(client, root / "f.txt", offset=0).status_code == 404


def test_parent_swapped_for_a_symlink_after_the_check_is_refused(client, root, tmp_path, monkeypatch):
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside\n")
    sub = root / "sub"
    sub.mkdir()
    (sub / "secret.txt").write_text("inside\n")
    real = fsguard.resolve_under_roots

    def check_then_swap(raw, **kw):
        p = real(raw, **kw)
        (sub / "secret.txt").unlink()
        sub.rmdir()
        sub.symlink_to(outside)          # 판정 뒤 부모 디렉토리를 경계 밖 링크로
        return p

    monkeypatch.setattr(files_routes.fsguard, "resolve_under_roots", check_then_swap)
    r = _get(client, sub / "secret.txt", offset=0)
    assert r.status_code == 404
    assert "outside" not in r.text


def test_last_component_swapped_for_a_symlink_is_refused(client, root, tmp_path, monkeypatch):
    outside = tmp_path.parent / f"{tmp_path.name}-outside2"
    outside.mkdir()
    (outside / "s.txt").write_text("outside\n")
    target = root / "t.txt"
    target.write_text("inside\n")
    real = fsguard.resolve_under_roots

    def check_then_swap(raw, **kw):
        p = real(raw, **kw)
        target.unlink()
        target.symlink_to(outside / "s.txt")
        return p

    monkeypatch.setattr(files_routes.fsguard, "resolve_under_roots", check_then_swap)
    r = _get(client, target)
    assert r.status_code == 404 and "outside" not in r.text


def test_symlink_inside_the_root_still_opens(client, root):
    (root / "real.txt").write_text("ok\n")
    (root / "link.txt").symlink_to(root / "real.txt")
    d = _get(client, root / "link.txt", offset=0).json()
    assert d["content"] == "ok\n" and d["path"] == str(root / "real.txt")


def test_read_runs_in_the_fs_pool(client, root, monkeypatch):
    """경로 판정부터 읽기까지 `fsops.call`(fs 풀, 기한, 차단기) 안에서 돈다."""
    from resilience import fsops
    seen = {}
    real = fsops.call

    async def spy(fn, *a, **kw):
        seen["op"] = kw.get("op")
        return await real(fn, *a, **kw)

    monkeypatch.setattr(files_routes.fsops, "call", spy)
    (root / "a.txt").write_text("x\n")
    assert _get(client, root / "a.txt", offset=0).status_code == 200
    assert seen["op"] == "fs.file"


def test_path_is_read_through_open_resolved(root, monkeypatch):
    calls = []
    real = fsguard.open_resolved
    monkeypatch.setattr(fs_read.fsguard, "open_resolved", lambda p: calls.append(p) or real(p))
    p = root / "a.txt"
    p.write_text("x\n")
    fs_read.read(Path(p))
    assert calls == [p]
