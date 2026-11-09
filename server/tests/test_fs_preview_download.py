"""파일 개편 단계 7 — 미리보기 바이트(`/api/fs/raw`: SVG·미디어·Range)와 내려받기(`/api/fs/download`).

계약은 API.md, 정책은 docs/ref/files.md 「Previews and downloads」. 여기서 보는 것:
- SVG는 sandbox CSP와 함께만 나간다. 이름만 바꾼 파일(확장자 ≠ 매직 바이트)은 거부한다.
- 미디어 Range: 206·`Content-Range`, 끝에서 센 구간, 416, 여러 구간 → 200, `If-Range` 불일치 → 200.
- 보내는 도중 파일이 바뀌면 끊는다. 끝나거나 끊겨도 fd가 닫힌다.
- 내려받기: attachment·nosniff·no-store·CSP·octet-stream, 한글 이름(RFC 5987), `rev` 불일치 409, 상한 413,
  경계 밖·거부 이름 403, 판정 뒤 심링크 바꿔치기 404(`open_resolved`).
"""

import os
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import file_store
import fs_stream
import fsguard
import main

MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + bytes(range(256)) * 8
WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 60
MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x01" * 50
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 40
SVG = b"<?xml version='1.0'?>\n<svg xmlns='http://www.w3.org/2000/svg' onload='alert(1)'><script>alert(2)</script></svg>"


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


def _raw(client, p, headers=None):
    return client.get("/api/fs/raw", params={"path": str(p)}, headers=headers)


def _dl(client, p, **params):
    return client.get("/api/fs/download", params={"path": str(p), **params})


# --- 타입 판정 -------------------------------------------------------------------------


@pytest.mark.parametrize("name,data,mime", [
    ("a.mp4", MP4, "video/mp4"),
    ("a.m4a", MP4, "audio/mp4"),
    ("a.webm", WEBM, "video/webm"),
    ("a.mp3", MP3, "audio/mpeg"),
    ("b.mp3", b"\xff\xfb\x90\x00" + b"\x00" * 20, "audio/mpeg"),
    ("a.wav", WAV, "audio/wav"),
    ("a.svg", SVG, "image/svg+xml"),
])
def test_raw_type_accepts_matching_extension_and_magic(name, data, mime):
    assert fsguard.raw_type(Path(name), data) == mime


@pytest.mark.parametrize("name,data", [
    ("a.mp4", b"<html><script>alert(1)</script></html>"),   # 이름만 바꾼 HTML
    ("a.webm", MP4),                                          # 다른 형식의 매직
    ("a.wav", b"RIFF\x00\x00\x00\x00WEBPVP8 "),              # RIFF지만 WAVE가 아님
    ("a.mp3", b"just text"),
    ("a.svg", b"not markup at all"),
    ("a.svg", b"<svg\x00binary"),
    ("a.mov", MP4),                                           # 목록 밖 확장자
    ("a.html", b"<svg xmlns='http://www.w3.org/2000/svg'/>"),
    ("a.pdf", b"%PDF-1.7"),
])
def test_raw_type_refuses_mismatches(name, data):
    assert fsguard.raw_type(Path(name), data) is None


def test_fs_file_reports_media_so_the_viewer_plays_it(client, root):
    (root / "clip.mp4").write_bytes(MP4)
    d = client.get("/api/fs/file", params={"path": str(root / "clip.mp4"), "offset": 0}).json()
    assert d["binary"] is True and d["image"] is False
    assert d["media"] == "video" and d["mime"] == "video/mp4"


# --- /api/fs/raw: SVG ---------------------------------------------------------------------


def test_svg_is_served_as_an_image_under_sandbox(client, root):
    (root / "logo.svg").write_bytes(SVG)
    r = _raw(client, root / "logo.svg")
    assert r.status_code == 200 and r.content == SVG
    assert r.headers["content-type"].startswith("image/svg+xml")
    assert r.headers["content-disposition"] == "inline"
    assert r.headers["x-content-type-options"] == "nosniff"
    csp = r.headers["content-security-policy"]
    for part in ("sandbox", "default-src 'none'", "frame-ancestors 'none'"):
        assert part in csp
    assert "allow-scripts" not in csp and "script-src" not in csp
    # 앱 기본 CSP(frame-ancestors만)가 이 정책을 덮어쓰지 않는다 — 헤더가 하나다.
    assert len(r.headers.get_list("content-security-policy")) == 1


def test_svg_over_the_image_cap_is_413(client, root, monkeypatch):
    monkeypatch.setattr(fsguard, "MAX_IMAGE_BYTES", 10)
    (root / "big.svg").write_bytes(SVG)
    assert _raw(client, root / "big.svg").status_code == 413


def test_html_named_svg_is_refused(client, root):
    (root / "x.svg").write_text("hello <b>not svg</b>")
    assert _raw(client, root / "x.svg").status_code == 403


def test_pdf_is_never_previewed(client, root):
    """PDF는 다운로드만(결정 D8) — 브라우저 PDF 보기가 문서 안 스크립트를 돌릴 수 있다."""
    (root / "a.pdf").write_bytes(b"%PDF-1.7\n%\xe2\xe3\n1 0 obj<</JS(app.alert(1))>>endobj")
    assert _raw(client, root / "a.pdf").status_code == 403


# --- /api/fs/raw: 미디어 Range --------------------------------------------------------------


def test_media_without_range_is_200_whole(client, root):
    (root / "a.mp4").write_bytes(MP4)
    r = _raw(client, root / "a.mp4")
    assert r.status_code == 200 and r.content == MP4
    assert r.headers["accept-ranges"] == "bytes"
    assert r.headers["content-length"] == str(len(MP4))
    assert r.headers["etag"].startswith('"') and not r.headers["etag"].startswith("W/")


def test_media_single_range_is_206(client, root):
    (root / "a.mp4").write_bytes(MP4)
    r = _raw(client, root / "a.mp4", headers={"Range": "bytes=10-19"})
    assert r.status_code == 206
    assert r.content == MP4[10:20]
    assert r.headers["content-range"] == f"bytes 10-19/{len(MP4)}"
    assert r.headers["content-length"] == "10"


def test_media_open_ended_and_suffix_ranges(client, root):
    (root / "a.mp4").write_bytes(MP4)
    r = _raw(client, root / "a.mp4", headers={"Range": f"bytes={len(MP4) - 5}-"})
    assert r.status_code == 206 and r.content == MP4[-5:]
    r = _raw(client, root / "a.mp4", headers={"Range": "bytes=-7"})
    assert r.status_code == 206 and r.content == MP4[-7:]
    assert r.headers["content-range"] == f"bytes {len(MP4) - 7}-{len(MP4) - 1}/{len(MP4)}"
    # 끝을 넘는 끝값은 파일 끝으로 줄인다.
    r = _raw(client, root / "a.mp4", headers={"Range": "bytes=0-999999999"})
    assert r.status_code == 206 and r.content == MP4


@pytest.mark.parametrize("rng", [f"bytes={len(MP4)}-", "bytes=999999-1000000", "bytes=-0"])
def test_media_range_outside_the_file_is_416(client, root, rng):
    (root / "a.mp4").write_bytes(MP4)
    r = _raw(client, root / "a.mp4", headers={"Range": rng})
    assert r.status_code == 416
    assert r.headers["content-range"] == f"bytes */{len(MP4)}"


@pytest.mark.parametrize("rng", ["bytes=0-1,5-9", "items=0-5", "bytes=abc", "bytes=9-3", "bytes=-"])
def test_media_multi_or_malformed_range_is_200_whole(client, root, rng):
    (root / "a.mp4").write_bytes(MP4)
    r = _raw(client, root / "a.mp4", headers={"Range": rng})
    assert r.status_code == 200 and r.content == MP4


def test_media_if_range(client, root):
    (root / "a.mp4").write_bytes(MP4)
    etag = _raw(client, root / "a.mp4").headers["etag"]
    r = _raw(client, root / "a.mp4", headers={"Range": "bytes=0-3", "If-Range": etag})
    assert r.status_code == 206 and r.content == MP4[:4]
    for stale in ('"other"', "W/" + etag, "Wed, 21 Oct 2015 07:28:00 GMT"):
        r = _raw(client, root / "a.mp4", headers={"Range": "bytes=0-3", "If-Range": stale})
        assert r.status_code == 200 and r.content == MP4


def test_media_is_not_capped_like_images(client, root, monkeypatch):
    monkeypatch.setattr(fsguard, "MAX_IMAGE_BYTES", 10)
    (root / "a.mp4").write_bytes(MP4)
    assert _raw(client, root / "a.mp4").status_code == 200


def test_media_renamed_html_is_refused(client, root):
    (root / "evil.mp4").write_text("<html><script>alert(1)</script></html>")
    assert _raw(client, root / "evil.mp4").status_code == 403


def test_parse_range_unit():
    assert fs_stream.parse_range(None, 10) is None
    assert fs_stream.parse_range("bytes=2-4", 10) == (2, 4)
    assert fs_stream.parse_range("bytes=2-", 10) == (2, 9)
    assert fs_stream.parse_range("bytes=-3", 10) == (7, 9)
    assert fs_stream.parse_range("bytes=-30", 10) == (0, 9)
    assert fs_stream.parse_range("bytes=1-2,4-5", 10) is None
    with pytest.raises(fs_stream.Unsatisfiable):
        fs_stream.parse_range("bytes=10-", 10)
    with pytest.raises(fs_stream.Unsatisfiable):
        fs_stream.parse_range("bytes=-1", 0)


# --- 스트림: 바뀌면 끊는다, fd는 닫힌다 ------------------------------------------------------


def _open_fds() -> int:
    return len(os.listdir("/dev/fd"))


def test_stream_closes_the_fd_after_a_full_send(client, root):
    (root / "a.mp4").write_bytes(MP4)
    _raw(client, root / "a.mp4")            # 한 번 데워 둔다(지연 import·풀 스레드)
    before = _open_fds()
    for _ in range(5):
        assert _raw(client, root / "a.mp4", headers={"Range": "bytes=0-9"}).status_code == 206
        assert _dl(client, root / "a.mp4").status_code == 200
    assert _open_fds() <= before + 1


def test_stream_aborts_when_the_file_changes_mid_send(root, monkeypatch):
    """조각 사이 rev가 바뀌면 StreamChanged로 끝난다 — 바뀐 바이트를 섞어 「다 받음」으로 주지 않는다."""
    import asyncio

    monkeypatch.setattr(fs_stream, "CHUNK", 16)
    p = root / "grow.mp4"
    p.write_bytes(MP4)

    async def run():
        out = await fs_stream.open_for("raw", str(p))
        assert isinstance(out, fs_stream.Opened)
        got = []
        with pytest.raises(fs_stream.StreamChanged):
            async for piece in fs_stream.stream(out, 0, out.size, path=str(p), op="test"):
                got.append(piece)
                if len(got) == 2:
                    with open(p, "ab") as f:
                        f.write(b"more")
        assert len(got) == 2
        with pytest.raises(OSError):
            os.fstat(out.fd)                 # 끊긴 뒤에도 fd가 닫혔다

    asyncio.run(run())


def test_open_failure_closes_the_fd(root):
    """허용 타입이 아니어서 돌려보낸 경우에도 연 fd가 남지 않는다."""
    import asyncio

    (root / "a.txt").write_text("hi")
    before = _open_fds()

    async def run():
        for _ in range(5):
            assert await fs_stream.open_for("raw", str(root / "a.txt")) == ("not_allowed",)

    asyncio.run(run())
    assert _open_fds() <= before


# --- /api/fs/download -----------------------------------------------------------------------


def test_download_headers(client, root):
    body = b"<html><script>alert(1)</script></html>"
    (root / "page.html").write_bytes(body)
    r = _dl(client, root / "page.html")
    assert r.status_code == 200 and r.content == body
    assert r.headers["content-type"] == "application/octet-stream"     # 문서로 열리지 않는다
    assert r.headers["content-disposition"].startswith("attachment;")
    assert 'filename="page.html"' in r.headers["content-disposition"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "no-store"
    assert "sandbox" in r.headers["content-security-policy"]
    assert r.headers["content-length"] == str(len(body))
    assert r.headers["x-fs-rev"]


def test_download_keeps_a_korean_name_and_neutralises_quotes(client, root):
    (root / '보고서 "최종".txt').write_text("x")
    r = _dl(client, root / '보고서 "최종".txt')
    assert r.status_code == 200
    cd = r.headers["content-disposition"]
    assert "filename*=UTF-8''%EB%B3%B4%EA%B3%A0%EC%84%9C%20%22%EC%B5%9C%EC%A2%85%22.txt" in cd
    ascii_part = cd.split('filename="', 1)[1].split('"', 1)[0]
    assert '"' not in ascii_part and "\\" not in ascii_part


def test_download_empty_file(client, root):
    (root / "empty.bin").write_bytes(b"")
    r = _dl(client, root / "empty.bin")
    assert r.status_code == 200 and r.content == b""


def test_download_rev_must_match_what_was_seen(client, root):
    p = root / "a.txt"
    p.write_text("one\n")
    rev = client.get("/api/fs/file", params={"path": str(p), "offset": 0}).json()["rev"]
    assert _dl(client, p, rev=rev).status_code == 200
    p.write_text("two, longer\n")
    r = _dl(client, p, rev=rev)
    assert r.status_code == 409
    assert r.json()["error"] == "changed" and r.json()["rev"] != rev


def test_download_bad_rev_is_400(client, root):
    (root / "a.txt").write_text("x")
    assert _dl(client, root / "a.txt", rev="x" * 500).status_code == 400


def test_download_over_the_cap_is_413(client, root, monkeypatch):
    monkeypatch.setattr(file_store, "MAX_UPLOAD_BYTES", 3)
    (root / "a.txt").write_text("four")
    r = _dl(client, root / "a.txt")
    assert r.status_code == 413 and r.json()["error"] == "too_large"


def test_download_outside_the_root_is_denied(client, root, tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-dl-outside.txt"
    outside.write_text("secret")
    r = _dl(client, outside)
    assert r.status_code == 403 and "secret" not in r.text


@pytest.mark.parametrize("name", [".env", "id_rsa", "server.pem", ".env.local"])
def test_download_denied_names(client, root, name):
    (root / name).write_text("secret")
    r = _dl(client, root / name)
    assert r.status_code == 403 and "secret" not in r.text


def test_download_hidden_state_dir_is_denied(client, root):
    (root / ".sometool").mkdir()
    (root / ".sometool" / "auth.json").write_text("secret")
    assert _dl(client, root / ".sometool" / "auth.json").status_code == 403


def test_download_traversal_is_denied(client, root, tmp_path):
    (tmp_path.parent / f"{tmp_path.name}-x.txt").write_text("secret")
    r = _dl(client, f"{root}/../../{tmp_path.name}-x.txt")
    assert r.status_code == 403


def test_download_directory_and_missing_are_404(client, root):
    (root / "d").mkdir()
    assert _dl(client, root / "d").status_code == 404
    assert _dl(client, root / "nope.txt").status_code == 404


def test_download_parent_swapped_for_a_symlink_after_the_check(client, root, tmp_path, monkeypatch):
    outside = tmp_path.parent / f"{tmp_path.name}-dl-out"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside")
    sub = root / "sub"
    sub.mkdir()
    (sub / "secret.txt").write_text("inside")
    real = fsguard.resolve_under_roots

    def check_then_swap(raw, **kw):
        p = real(raw, **kw)
        (sub / "secret.txt").unlink()
        sub.rmdir()
        sub.symlink_to(outside)
        return p

    monkeypatch.setattr(fsguard, "resolve_under_roots", check_then_swap)
    r = _dl(client, sub / "secret.txt")
    assert r.status_code == 404 and "outside" not in r.text


def test_download_last_component_swapped_for_a_symlink(client, root, tmp_path, monkeypatch):
    outside = tmp_path.parent / f"{tmp_path.name}-dl-out2"
    outside.mkdir()
    (outside / "s.txt").write_text("outside")
    target = root / "t.svg"
    real = fsguard.resolve_under_roots

    def check_then_swap(raw, **kw):
        p = real(raw, **kw)
        target.unlink()
        target.symlink_to(outside / "s.txt")
        return p

    monkeypatch.setattr(fsguard, "resolve_under_roots", check_then_swap)
    for get in (_dl, _raw):
        if target.is_symlink():
            target.unlink()
        target.write_bytes(SVG)          # 판정 때는 경계 안의 진짜 파일
        r = get(client, target)
        assert r.status_code == 404 and "outside" not in r.text


def test_raw_swap_to_outside_media_is_refused(client, root, tmp_path, monkeypatch):
    outside = tmp_path.parent / f"{tmp_path.name}-raw-out"
    outside.mkdir()
    (outside / "a.mp4").write_bytes(MP4)
    sub = root / "m"
    sub.mkdir()
    (sub / "a.mp4").write_bytes(MP4)
    real = fsguard.resolve_under_roots

    def check_then_swap(raw, **kw):
        p = real(raw, **kw)
        (sub / "a.mp4").unlink()
        sub.rmdir()
        sub.symlink_to(outside)
        return p

    monkeypatch.setattr(fsguard, "resolve_under_roots", check_then_swap)
    assert _raw(client, sub / "a.mp4").status_code == 404


def test_symlink_inside_the_root_still_downloads(client, root):
    (root / "real.txt").write_text("ok")
    (root / "link.txt").symlink_to(root / "real.txt")
    r = _dl(client, root / "link.txt")
    assert r.status_code == 200 and r.content == b"ok"


def test_download_runs_its_io_in_the_fs_pool(client, root, monkeypatch):
    from resilience import fsops
    seen = []
    real = fsops.call

    async def spy(fn, *a, **kw):
        seen.append(kw.get("op"))
        return await real(fn, *a, **kw)

    monkeypatch.setattr(fsops, "call", spy)
    (root / "a.txt").write_text("x" * 10)
    assert _dl(client, root / "a.txt").status_code == 200
    assert "fs.download" in seen and "fs.download.read" in seen


# --- 이상한 Range·끊긴 연결: fd가 새지 않는다 --------------------------------------------------


def _raw_with_range_bytes(p, rng: str) -> int:
    """Range 헤더를 **바이트 그대로** 보낸다. TestClient는 헤더를 UTF-8로 바꿔 '²'(0xB2)가 'Â²'로 닿으므로,
    실제 서버(ASGI는 헤더를 latin-1로 푼다)와 같게 httpx의 ASGI 전송으로 바로 부른다."""
    import asyncio

    import httpx

    raw = rng.encode("latin-1") if all(ord(c) < 256 for c in rng) else rng.encode("utf-8")

    async def run():
        t = httpx.ASGITransport(app=main.app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=t, base_url="http://testserver") as ac:
            r = await ac.get("/api/fs/raw", params={"path": str(p)}, headers={"Range": raw})
            return r.status_code

    return asyncio.run(run())


@pytest.mark.parametrize("rng", [
    "bytes=²-",                       # str.isdigit()은 참, int()는 ValueError
    "bytes=0-²",
    "bytes=-١٢",                      # 아랍 숫자
    "bytes=" + "9" * 5000 + "-",      # int() 자릿수 상한(4300) 초과
    "bytes=0-" + "9" * 5000,
    "bytes=-" + "9" * 5000,
    "bytes=" + "0" * 5000 + "1-2",    # 앞의 0은 자릿수로 세지 않는다
    "bytes=-5-",
    "bytes=--5",
    "bytes=0-1,2-3,4-5",
])
def test_weird_ranges_never_500_and_never_leak_fds(client, root, rng):
    (root / "a.mp4").write_bytes(MP4)
    _raw_with_range_bytes(root / "a.mp4", "bytes=0-1")
    before = _open_fds()
    for _ in range(5):
        assert _raw_with_range_bytes(root / "a.mp4", rng) in (200, 206, 416)
    assert _open_fds() <= before + 1


def test_parse_range_huge_and_non_ascii_numbers():
    big = "9" * 5000
    assert fs_stream.parse_range("bytes=²-", 10) is None
    assert fs_stream.parse_range("bytes=-١٢", 10) is None
    assert fs_stream.parse_range("bytes=0-" + big, 10) == (0, 9)
    assert fs_stream.parse_range("bytes=-" + big, 10) == (0, 9)
    assert fs_stream.parse_range("bytes=" + "0" * 5000 + "2-3", 10) == (2, 3)
    assert fs_stream.parse_range("bytes=" + big + "-3", 10) is None       # 끝 < 시작 → 무시
    with pytest.raises(fs_stream.Unsatisfiable):
        fs_stream.parse_range("bytes=" + big + "-", 10)


def test_raw_closes_the_fd_when_building_the_reply_fails(root, monkeypatch):
    """open_for 뒤 응답을 만들다 무엇이 터지든 fd는 닫힌다."""
    (root / "a.mp4").write_bytes(MP4)
    opened = []
    real = fs_stream.open_for

    async def spy(*a, **kw):
        out = await real(*a, **kw)
        opened.append(out)
        return out

    def boom(*a, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(fs_stream, "open_for", spy)
    monkeypatch.setattr(fs_stream, "parse_range", boom)
    with TestClient(main.app, raise_server_exceptions=False) as c:
        assert _raw(c, root / "a.mp4", headers={"Range": "bytes=0-1"}).status_code == 500
    assert opened and opened[0].closed
    with pytest.raises(OSError):
        os.fstat(opened[0].fd)


@pytest.mark.parametrize("kind", ["raw", "download"])
def test_disconnect_before_the_body_starts_closes_the_fd(root, kind):
    """머리를 보내다 연결이 끊기면 본문 생성기는 한 번도 돌지 않는다 — 그래도 fd는 닫혀야 한다."""
    import asyncio

    p = root / "a.mp4"
    p.write_bytes(MP4)

    async def run():
        out = await fs_stream.open_for(kind, str(p), **({"cap": 1 << 30} if kind == "download" else {}))
        assert isinstance(out, fs_stream.Opened)
        resp = fs_stream.FdStreamingResponse(out, 0, out.size, path=str(p), op="test")

        async def receive():
            await asyncio.sleep(3600)

        async def send(msg):
            raise OSError("client went away")

        scope = {"type": "http", "asgi": {"spec_version": "2.4"}, "method": "GET"}
        with pytest.raises(Exception):
            await resp(scope, receive, send)
        assert out.closed
        with pytest.raises(OSError):
            os.fstat(out.fd)

    asyncio.run(run())


def test_close_twice_does_not_close_a_reused_fd(root):
    p = root / "a.txt"
    p.write_text("x")
    fd = os.open(p, os.O_RDONLY)
    o = fs_stream.Opened(fd=fd, size=1, rev="r")
    fs_stream.close(o)
    again = os.open(p, os.O_RDONLY)        # 같은 번호를 다시 받을 수 있다
    try:
        fs_stream.close(o)
        os.fstat(again)                    # 남의 fd는 살아 있다
    finally:
        os.close(again)


def test_raw_head_is_not_routed(client, root):
    """HEAD는 받지 않는다(쓰는 클라이언트가 없다) — 죽은 분기를 지웠다."""
    (root / "a.mp4").write_bytes(MP4)
    assert client.head("/api/fs/raw", params={"path": str(root / "a.mp4")}).status_code == 405
