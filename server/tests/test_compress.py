"""응답 압축(server/compress.py) — 텍스트만 줄이고, 비밀이 든 경로·이진·이어받기·작은 응답은 그대로 둔다."""
import gzip
import json

import pytest
from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse
from starlette.testclient import TestClient

from compress import CompressMiddleware

BIG = {"rows": [{"name": f"session-{i}", "state": "running", "path": "/Users/x/proj"} for i in range(400)]}
GZ = {"Accept-Encoding": "gzip"}


@pytest.fixture
def client():
    app = FastAPI()

    @app.get("/api/big")
    def big():
        return JSONResponse(BIG)

    @app.api_route("/api/headable", methods=["GET", "HEAD"])
    def headable():
        return JSONResponse(BIG)

    @app.get("/api/small")
    def small():
        return {"ok": True}

    @app.get("/api/auth/status")
    def auth():
        return JSONResponse(BIG)

    @app.get("/api/pairs/{pair_id}/status")
    def pairs(pair_id: str):
        return JSONResponse(BIG)

    @app.get("/api/blob")
    def blob():
        return Response(b"x" * 5000, media_type="application/octet-stream")

    @app.get("/api/image")
    def image():
        return Response(b"x" * 5000, media_type="image/png")

    @app.get("/api/partial")
    def partial():
        return Response(b"x" * 5000, status_code=206, media_type="text/plain", headers={"Content-Range": "bytes 0-4999/9999"})

    @app.get("/api/already")
    def already():
        return Response(gzip.compress(b"y" * 5000), media_type="text/plain", headers={"Content-Encoding": "gzip"})

    @app.get("/api/text")
    def text():
        return Response("한글 텍스트 " * 600, media_type="text/css; charset=utf-8", headers={"ETag": '"abc"'})

    app.add_middleware(CompressMiddleware)
    return TestClient(app)


def test_large_json_is_compressed_and_round_trips(client):
    r = client.get("/api/big", headers=GZ)
    assert r.headers["content-encoding"] == "gzip"
    assert "Accept-Encoding" in r.headers["vary"]
    assert r.json() == BIG                       # httpx가 풀어서 준다 — 내용이 그대로
    assert int(r.headers["content-length"]) < len(json.dumps(BIG)) / 3


def test_no_compression_unless_the_client_accepts_it(client):
    r = client.get("/api/big", headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in r.headers
    assert r.json() == BIG


def test_small_responses_are_left_alone(client):
    r = client.get("/api/small", headers=GZ)
    assert "content-encoding" not in r.headers and r.json() == {"ok": True}


@pytest.mark.parametrize("path", ["/api/auth/status", "/api/pairs/p1/status"])
def test_credential_paths_are_never_compressed(client, path):
    r = client.get(path, headers=GZ)
    assert "content-encoding" not in r.headers
    assert r.json() == BIG


@pytest.mark.parametrize("path", ["/api/blob", "/api/image", "/api/partial", "/api/already"])
def test_binary_partial_and_already_encoded_are_untouched(client, path):
    r = client.get(path, headers=GZ)
    if path == "/api/already":
        assert r.headers["content-encoding"] == "gzip" and r.content == b"y" * 5000
    else:
        assert "content-encoding" not in r.headers
        assert len(r.content) == 5000


def test_conditional_validators_survive_compression(client):
    r = client.get("/api/text", headers=GZ)
    assert r.headers["content-encoding"] == "gzip" and r.headers["etag"] == '"abc"'
    assert r.text == "한글 텍스트 " * 600


def test_head_request_still_works(client):
    r = client.head("/api/headable", headers=GZ)
    assert r.status_code == 200 and r.content == b""


def test_the_real_app_compresses_its_static_files():
    """실제 앱의 미들웨어 순서에서도 정적 파일이 압축된다(다른 미들웨어가 응답을 가로채지 않는다)."""
    import main
    c = TestClient(main.app, base_url="http://127.0.0.1")
    r = c.get("/static/index.html", headers=GZ)
    if r.status_code != 200:
        pytest.skip(f"정적 파일을 못 읽음({r.status_code})")
    assert r.headers.get("content-encoding") == "gzip"
    assert r.headers.get("x-content-type-options") == "nosniff", "보안 헤더가 압축 응답에도 붙는다"
    assert r.text.lstrip().lower().startswith("<!doctype html")
