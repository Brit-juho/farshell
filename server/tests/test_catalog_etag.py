"""5초 폴링 응답의 조건부 조회 — 내용 해시 ETag, 같으면 본문 없는 304, 다르면 200. `no-store`는 그대로다."""
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VT_STATE_DIR", str(home / "state"))
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(home))
    from session_catalog import db
    db.snapshot()
    from compress import CompressMiddleware
    from routes import session_catalog
    app = FastAPI()
    # 라우터에 **실제로 붙은** 인증 함수를 우회한다 — `auth.require_human_session`을 이름으로 찾으면, 다른 시험이 `auth`를
    # importlib.reload한 뒤에는 라우터가 쥔 옛 함수와 달라 우회가 무시되고 진짜 검사가 돌아 403이 난다.
    for gate in session_catalog.router.dependencies:
        app.dependency_overrides[gate.dependency] = lambda: None
    app.include_router(session_catalog.router)
    app.add_middleware(CompressMiddleware)
    return TestClient(app)


PATHS = ["/api/session-catalog?archived=false&limit=200&order=activity", "/api/session-catalog/projects"]


@pytest.mark.parametrize("path", PATHS)
def test_the_first_answer_carries_an_etag_and_stays_no_store(client, path):
    r = client.get(path)
    assert r.status_code == 200
    assert r.headers["etag"].startswith('W/"')
    assert r.headers["cache-control"] == "no-store"      # 브라우저 디스크 캐시를 열지 않는다


@pytest.mark.parametrize("path", PATHS)
def test_the_same_content_answers_304_without_a_body(client, path):
    etag = client.get(path).headers["etag"]
    r = client.get(path, headers={"If-None-Match": etag})
    assert r.status_code == 304 and r.content == b""
    assert r.headers["etag"] == etag and r.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("form", ["{e}", "{e}, W/\"zzz\"", "\"nope\", {e}", "{w}", "*"])
def test_if_none_match_forms(client, form):
    etag = client.get(PATHS[0]).headers["etag"]
    header = form.format(e=etag, w=etag.removeprefix("W/"))
    assert client.get(PATHS[0], headers={"If-None-Match": header}).status_code == 304


def test_other_etags_get_the_full_answer(client):
    r = client.get(PATHS[0], headers={"If-None-Match": 'W/"not-this-one"'})
    assert r.status_code == 200 and "sessions" in r.json()


def test_a_change_in_the_content_changes_the_etag(client):
    """바뀐 내용을 304로 가리지 않는다 — 이 시험이 revision이 아니라 내용 해시를 쓴 이유다."""
    from session_catalog import db
    first = client.get(PATHS[0])
    db.configure({"auto_archive_days": 12})
    r = client.get(PATHS[0], headers={"If-None-Match": first.headers["etag"]})
    assert r.status_code == 200 and r.headers["etag"] != first.headers["etag"]
    assert r.json()["auto_archive_days"] == 12


def test_a_write_that_never_touches_the_change_log_still_shows_up(client):
    """`change_log` 번호(revision)를 올리지 않고 직접 쓴 값도 내용이 바뀌었으면 304가 되지 않는다(revision 방식이면 낡은 목록이 남는다)."""
    from session_catalog.db import database
    first = client.get(PATHS[0])
    with database(write=True) as con:
        con.execute("UPDATE meta SET value=value WHERE key='host'")     # 번호가 오르지 않는 쓰기
        con.execute("INSERT INTO meta VALUES('ended_cleanup_days','30') ON CONFLICT(key) DO UPDATE SET value='30'")
    assert client.get(PATHS[0], headers={"If-None-Match": first.headers["etag"]}).status_code == 200


def test_compression_and_304_work_together(client):
    gz = {"Accept-Encoding": "gzip"}
    first = client.get(PATHS[0], headers=gz)
    etag = first.headers["etag"]
    assert client.get(PATHS[0], headers={**gz, "If-None-Match": etag}).status_code == 304
    # 압축 여부와 상관없이 같은 내용은 같은 ETag
    assert client.get(PATHS[0], headers={"Accept-Encoding": "identity"}).headers["etag"] == etag


def test_writes_and_other_routes_carry_no_etag(client):
    r = client.post("/api/session-catalog/projects/" + "a" * 32, json={"hidden": True})
    assert "etag" not in r.headers
