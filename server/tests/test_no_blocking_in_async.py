"""async 핸들러가 이벤트 루프에서 동기 블로킹을 부르지 못하게 막는 가드.

2026-09-16에 같은 결함을 **세 번** 찾았다. 워크트리 탐색(`/api/worktrees`),
tmux 세션 목록(`/api/tmux/sessions`), 그리고 클라이언트 목록
(`/api/tmux/clients`). 마지막 것은 블로킹이 `_client_rows`라는 동기 헬퍼
**한 겹 아래**에 있어서 눈으로 하는 감사에서 빠졌다.

증상은 언제나 같다: 그 호출이 도는 동안 **서버 전체**가 멈춘다 — HTTP도,
WebSocket도, PTY 입출력 브로드캐스트도. 웹 터미널에서는 이것이 "타이핑이
멎었다가 한 번에 쏟아지는" 현상으로 나타난다. 실측(2026-09-16):

    폴링 부하 + 동시 타이핑 28초        수정 전      수정 후
      키 입력 왕복 p90                  829 ms        9 ms
      1초 넘게 멈춘 키                   25개          0개
      한 번에 몰려 온 최대 글자 수       20글자        0
      이벤트 루프가 막힌 시간(40초 중)   16.4초       0.6초

이 테스트는 "다음 번 핸들러"가 같은 실수를 반복하는 것을 막는다. 고치는 법은
`tmux_runner.run_async` / `run_text_async` / `get_all_panes_async` /
`has_session_async`를 쓰거나, 동기 헬퍼째로 `asyncio.to_thread(helper, ...)`에
넘기는 것이다(`routes/hosts.py`와 `routes/peer.py`가 그 관용구의 본보기다).
"""

from __future__ import annotations

import ast
import pathlib

SERVER = pathlib.Path(__file__).resolve().parent.parent

# 호출하는 순간 스레드가 멈추는 것들. 이름은 `모듈.함수` 또는 `함수`로 적는다.
BLOCKING = {
    "tmux_runner.run", "tmux_runner.run_text",
    "tmux_runner.get_all_panes", "tmux_runner.has_session",
    "tmux_runner.list_sessions", "tmux_runner.set_option",
    "subprocess.run", "subprocess.check_output", "subprocess.check_call",
    "subprocess.call", "subprocess.Popen",
    "worktree.list_worktrees",
    # 서브프로세스를 도는 상태 프로브. 실측 53.6ms / 32.9ms라 둘만으로
    # `/api/capabilities`가 87ms를 먹었다(2026-09-16).
    "tailscale.get_status", "tailscale.get_status_dict",
    "tailscale.get_ip", "tailscale.get_hostname",
    "tunnel.get_tunnel_status", "tunnel.find_active_pids",
}

# 스레드로 넘겨주는 것들 — 여기 첫 인자로 들어간 함수는 안전하다.
DISPATCHERS = {"to_thread", "run_in_executor"}

MAX_DEPTH = 3   # 동기 헬퍼를 몇 겹까지 따라 들어갈지


def _qual(node: ast.AST) -> str:
    if isinstance(node, ast.Attribute):
        base = _qual(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _direct_children(fn: ast.AST):
    """함수 본문을 훑되 **중첩 함수 정의 안으로는 안 들어간다.**

    중첩 sync 함수는 그 자체로 도는 게 아니라 to_thread에 실려 가는 경우가
    대부분이고(`routes/peer.py`의 `_solo`), 직접 호출되면 아래 호출 추적이
    잡아낸다. 여기서 같이 훑으면 안전한 코드까지 위반으로 잡힌다 —
    실제로 그 오탐 때문에 peer.py를 고칠 뻔했다.
    """
    for node in ast.iter_child_nodes(fn):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        yield node
        yield from _direct_children(node)


def _collect_funcs(tree: ast.AST) -> dict:
    """이름 → FunctionDef (중첩 포함). 동기 헬퍼를 따라 들어가기 위한 것."""
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            out.setdefault(node.name, node)
    return out


def _threaded_names(fn: ast.AST) -> set:
    """to_thread/run_in_executor에 넘겨진 함수 이름들(중첩 정의 안쪽까지 본다)."""
    names = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and _qual(node.func).split(".")[-1] in DISPATCHERS:
            for arg in node.args:
                q = _qual(arg)
                if q:
                    names.add(q.split(".")[-1])
    return names


def _scan(fn, funcs, threaded, depth, seen) -> list:
    hits = []
    for node in _direct_children(fn):
        if not isinstance(node, ast.Call):
            continue
        q = _qual(node.func)
        short = q.split(".")[-1]
        if q in BLOCKING:
            hits.append((q, node.lineno))
            continue
        # 같은 파일의 동기 헬퍼를 직접 부른 경우 — 그 안도 본다.
        if short in threaded or short in seen or depth >= MAX_DEPTH:
            continue
        helper = funcs.get(short)
        if helper is not None:
            # ⚠ 호출자의 threaded 집합을 물려주면 안 된다. 바깥 함수가 어딘가에서
            # `to_thread(_git, ...)`를 했다는 이유로, **다른 헬퍼 안의** 동기
            # `_git()` 호출까지 안전하다고 오판한다 — 실제로 이 버그 때문에
            # git_stage/git_unstage를 놓쳤다. 각 함수의 범위만 본다.
            inner = _scan(helper, funcs, _threaded_names(helper),
                          depth + 1, seen | {short})
            hits.extend((f"{short}() → {name}", line) for name, line in inner)
    return hits


def _violations() -> list:
    out = []
    targets = sorted((SERVER / "routes").glob("*.py")) + [SERVER / "main.py"]
    for path in targets:
        tree = ast.parse(path.read_text())
        funcs = _collect_funcs(tree)
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.AsyncFunctionDef):
                continue
            hits = _scan(fn, funcs, _threaded_names(fn), 0, {fn.name})
            for what, line in hits:
                out.append(f"{path.name}:{line}  async {fn.name}() → {what}")
    return sorted(out)


def test_no_async_handler_blocks_the_event_loop():
    found = _violations()
    assert found == [], (
        "async 핸들러가 이벤트 루프에서 동기 블로킹을 호출한다 — 그동안 서버 전체가 멈춘다"
        "(터미널 타이핑이 멎었다가 한 번에 쏟아진다):\n  "
        + "\n  ".join(found)
        + "\n\n고치는 법: tmux_runner.*_async 를 쓰거나, 동기 헬퍼째로"
          " asyncio.to_thread(helper, ...) 에 넘긴다."
    )


def test_guard_actually_detects_a_violation(tmp_path, monkeypatch):
    """가드가 실제로 잡는지 — 위반을 심어 확인한다.

    이 검사가 없으면 분석기가 조용히 아무것도 안 잡는 상태로 썩어도 모른다
    (`_direct_children`이 중첩 함수를 건너뛰므로 특히 그렇다).
    """
    routes = tmp_path / "routes"
    routes.mkdir()
    (routes / "bad.py").write_text(
        "import tmux_runner\n"
        "def _helper():\n"
        "    return tmux_runner.run_text(['list-sessions'])\n"
        "async def handler():\n"
        "    return _helper()\n"
    )
    (tmp_path / "main.py").write_text("")
    monkeypatch.setattr(pathlib.Path, "resolve", lambda self: self)
    import sys
    mod = sys.modules[__name__]
    monkeypatch.setattr(mod, "SERVER", tmp_path)
    found = _violations()
    assert any("handler" in f and "run_text" in f for f in found), (
        f"헬퍼 한 겹 아래의 블로킹을 못 잡았다 — 실제로 놓쳤던 종류다. found={found}"
    )
