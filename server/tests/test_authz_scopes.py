"""권한 어휘 단순화 — scope 17개 + 조건, 옛 67개 이름 변환, 필요 조건 값 누락 거부, elevated 판정, 프리셋, 주체 종류 5개,
감사 신원 문자열, 스키마 2 백업·복원 왕복."""

from __future__ import annotations

import ast
import hashlib
import json
import pathlib

import pytest

from authz import audit, defaults, engine, migrate, principals as P, scopes as S, store
from authz import __main__ as cli

NOW = 1_000_000.0

# 옛 카탈로그 67개 — 변환 표가 하나도 빠뜨리지 않았는지 코드와 따로 적어 둔 사본이다.
OLD_67 = (
    "session.list session.meta session.read session.stream session.input.line session.input.raw session.queue "
    "session.create session.kill session.rename session.clients.view session.clients.detach "
    "msg.send msg.read deleg.spawn deleg.control deleg.report fs.read fs.write fs.commit fs.drop "
    "browser.tab.open browser.tab.navigate browser.tab.read browser.tab.input browser.tab.close browser.js "
    "browser.cookies browser.storage browser.network browser.download browser.attach "
    "authz.request authz.admin authz.audit pair.manage agent.event agent.report ping "
    "relay.connect relay.sessions relay.tmux relay.tmux.kill relay.tmux.preview relay.agents relay.files "
    "relay.files.write relay.fs relay.git relay.git.write relay.ports relay.queue relay.snippets relay.search "
    "relay.usage relay.workspace relay.worktrees relay.repos relay.status relay.mcp relay.watch "
    "relay.ws.term relay.ws.agent relay.ws.workspace relay.ws.preview relay.ws.fswatch api.access").split()

OLD_PEER_VIEW = ("session.list", "session.meta", "session.read", "session.stream", "session.clients.view", "ping")
OLD_PEER_CONTROL = OLD_PEER_VIEW + ("session.input.raw", "session.create", "session.kill", "session.rename",
                                    "session.clients.detach", "fs.drop", "relay.*")


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "vt"))
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "")
    return tmp_path / "vt"


def snap(grants=(), **kw):
    return store.Snapshot(grants=list(grants), now=NOW, **kw)


# --- 변환 표: 67개 전부 --------------------------------------------------------------------------


def test_embedded_catalog_is_67_and_matches_the_code():
    assert len(OLD_67) == 67 == len(set(OLD_67))
    assert set(S.LEGACY_CATALOG) == set(OLD_67)


def test_every_old_name_maps_to_the_17_or_is_outside_the_vocabulary():
    assert set(S.LEGACY_TO_COARSE) == set(OLD_67)
    assert len(S.CATALOG) == 17
    targets = set()
    for name in OLD_67:
        scope, conds = S.LEGACY_TO_COARSE[name]
        if name in ("ping", "api.access"):
            assert scope == "internal" and name in S.INTERNAL and not S.known("msg.read")
            continue
        if name == "msg.read":
            assert scope is None                      # 본인 기록이라 scope 가 아니다
            continue
        assert scope in S.CATALOG, name
        targets.add(scope)
        keys = {c.key for c in S.COND_SPEC.get(scope, ())}
        assert set(conds) <= keys, (name, conds)       # 조건 키는 그 scope 가 선언한 것만
        for k, vs in conds.items():
            dom = {c.key: c.domain for c in S.COND_SPEC[scope]}[k]
            assert set(vs) <= set(dom), (name, k, vs)
    assert targets == S.CATALOG                        # 17개가 모두 옛 이름에서 나온다
    assert not S.known("msg.read") and not S.known("session.list")


def test_relay_old_names_cover_connect_plus_26_groups():
    relay = [n for n in OLD_67 if n.startswith("relay.")]
    assert len(relay) == 27 and len(S.ALL_RELAY_GROUPS) == 26
    assert {S.LEGACY_TO_COARSE[n][1].get("groups", [None])[0] for n in relay if n != "relay.connect"} == set(S.ALL_RELAY_GROUPS)


def test_wildcards_expand_over_old_names_then_map():
    sc, cd = S.translate_legacy(["session.*"])
    assert sc == ["session.view", "session.read", "session.input", "session.manage"]
    assert cd["session.input"] == {"modes": ["line", "raw"], "via": ["direct", "queue"]}
    assert set(cd["session.manage"]["ops"]) == set(S.MANAGE_OPS)
    sc, cd = S.translate_legacy(["relay.*"])
    assert sc == ["relay"] and cd["relay"]["groups"] == list(S.ALL_RELAY_GROUPS)
    sc, cd = S.translate_legacy(["*"])
    assert set(sc) == S.CATALOG
    # 조건을 적지 않은 이름은 기본값을 뜻한다 — input.line(직접) + queue 의 합집합은 via=[direct, queue]
    assert S.translate_legacy(["session.input.line", "session.queue"])[1]["session.input"] == {
        "modes": ["line"], "via": ["direct", "queue"]}
    assert S.translate_legacy(["session.queue"])[1]["session.input"] == {"modes": ["line"], "via": ["queue"]}
    assert S.translate_legacy(["session.input.line"])[1]["session.input"] == {"modes": ["line"]}


# --- 왕복: 옛 grant -> 새 grant -> 같은 판정 ---------------------------------------------------------

_RES = {"session": "session:local/t", "msg": "msg:local/t", "deleg.spawn": "deleg-quota:x", "deleg.control": "deleg:local/d",
        "fs": "fs:local/x", "browser": "browser:tab:1", "authz": "authz:*", "pair": "host:local", "agent": "session:local/t",
        "relay": "relay:local/g"}


def _resource(scope: str) -> str:
    for k in (scope, scope.split(".")[0]):
        if k in _RES:
            return _RES[k]
    raise KeyError(scope)


def _old_allow(grant, name) -> bool:
    return name in S.legacy_expand(grant)


def _new_allow(grant, name) -> bool:
    scope, ctx = S.coarse_call(name)
    assert scope is not None
    scopes, sconds = S.translate_legacy(grant)
    g = {"id": "g", "subject": "peer:t", "resource": "*", "scopes": scopes, "state": "active", "schema": 2}
    if sconds:
        g["sconds"] = sconds
    return engine.decide(P.peer("t"), scope, _resource(scope), ctx, snap([g])).allow


def _merged(name):
    for grp in S.LEGACY_MERGED_GROUPS:
        if name in grp:
            return grp
    return {name}


GRANT_SETS = (
    [[n] for n in OLD_67 if n not in ("ping", "api.access", "msg.read")]
    + [["session.*"], ["relay.*"], ["*"], list(OLD_PEER_VIEW), list(OLD_PEER_CONTROL),
       ["session.list", "session.read"], ["session.list", "session.read", "session.input.line"],
       ["session.input.raw", "session.queue"], ["session.input.line", "session.queue"], ["fs.write", "fs.commit"],
       ["deleg.control", "deleg.report"], ["browser.tab.open", "browser.cookies"], ["authz.audit"], ["agent.event"]])


def test_translate_round_trip_gives_the_same_decisions_for_all_67_names():
    callable_names = [n for n in OLD_67 if S.LEGACY_TO_COARSE[n][0] not in (None, "internal")]
    assert len(callable_names) == 64
    gaps = []
    for grant in GRANT_SETS:
        have = S.legacy_expand(grant)
        for m in callable_names:
            if m == "relay.connect":
                continue                                   # 연결 관문은 아래에서 따로 본다
            old = _old_allow(grant, m)
            new = _new_allow(grant, m)
            # 같은 (새 scope, 조건)으로 합쳐진 옛 이름끼리는 변환 뒤 구별되지 않는다 — 그 밖에는 정확히 같아야 한다.
            expect = any(x in have for x in _merged(m))
            assert new is expect, (grant, m, old, new)
            if old != new:
                gaps.append((tuple(grant), m))
    # 합쳐져 넓어지는 곳은 이름 묶음(view·read·admin·report)뿐이다
    for grant, m in gaps:
        assert len(_merged(m)) > 1, (grant, m)


def test_relay_connect_gate_follows_the_groups():
    # 그룹을 가진 grant 는 연결 관문도 통과한다 — relay.* 로 옮겨진 peer(control)는 옛 판정과 같다
    assert _new_allow(["relay.*"], "relay.connect") and _old_allow(["relay.*"], "relay.connect")
    assert not _new_allow(list(OLD_PEER_VIEW), "relay.connect") and not _old_allow(list(OLD_PEER_VIEW), "relay.connect")
    # 시스템이 만들지 않는 모양(connect 만, 또는 그룹만)은 새 규칙(그룹 지정이 관문)을 따른다: 문서화된 유일한 차이
    assert _old_allow(["relay.connect"], "relay.connect") and not _new_allow(["relay.connect"], "relay.connect")
    assert not _old_allow(["relay.sessions"], "relay.connect") and _new_allow(["relay.sessions"], "relay.connect")


def test_queue_and_line_stay_apart_after_translation():
    assert _new_allow(["session.queue"], "session.queue") and not _new_allow(["session.queue"], "session.input.line")
    assert _new_allow(["session.input.line"], "session.input.line") and not _new_allow(["session.input.line"], "session.queue")
    assert not _new_allow(["session.input.line"], "session.input.raw")
    assert _new_allow(["session.input.raw"], "session.input.line") and not _new_allow(["session.input.raw"], "session.queue")


def test_merged_conds_are_a_union_never_wider_than_the_old_names():
    sc, cd = S.translate_legacy(["session.create", "session.rename", "fs.drop", "browser.cookies", "browser.attach"])
    assert cd["session.manage"]["ops"] == ["create", "rename"]
    assert cd["fs.write"]["ops"] == ["drop"] and cd["browser.sensitive"]["ops"] == ["cookies", "attach"]
    # fs.drop 만으로는 읽기가 따라오지 않는다(옛 이름도 그랬다)
    assert not _new_allow(["fs.drop"], "fs.read") and not _new_allow(["fs.commit"], "fs.read")
    assert _new_allow(["fs.write"], "fs.read")


def test_msg_read_is_not_a_scope_and_is_dropped_from_stored_grants():
    rec = S.upgrade_record({"id": "x", "scopes": ["msg.send", "msg.read", "ping"], "subject": "agent:local/a"})
    assert rec["scopes"] == ["msg.send"] and rec["schema"] == 2


def test_upgrade_record_is_idempotent_and_leaves_new_records_alone():
    old = {"id": "g", "scopes": ["session.list", "session.read", "session.input.raw"], "x": 1}
    new = S.upgrade_record(old)
    assert S.upgrade_record(new) == new and S.upgrade_record(new) is new
    assert old["scopes"][0] == "session.list"              # 입력은 건드리지 않는다
    already = {"id": "n", "schema": 2, "scopes": ["session.*"]}
    assert S.upgrade_record(already) is already            # schema 2 의 와일드카드는 새 어휘 기준이라 다시 변환하지 않는다


# --- 필요 조건 값 누락 = 거부 ---------------------------------------------------------------------


def _full_grant():
    scopes = sorted(S.CATALOG)
    return {"id": "all", "subject": "peer:t", "resource": "*", "scopes": scopes, "schema": 2, "state": "active",
            "sconds": {s: {c.key: list(c.domain) for c in cs} for s, cs in S.COND_SPEC.items()}}


GOOD = {"session.input": {"mode": "raw"}, "session.manage": {"op": "kill"}, "deleg.control": {"role": "parent"},
        "fs.write": {"op": "write"}, "browser": {"op": "open"}, "browser.sensitive": {"op": "cookies"},
        "relay": {"group": "queue"}}


def test_required_conds_table_is_what_the_policy_declares():
    assert S.REQUIRED_CONDS == {"session.input": ("mode",), "session.manage": ("op",), "deleg.control": ("role",),
                                "fs.write": ("op",), "browser": ("op",), "browser.sensitive": ("op",),
                                "relay": ("group",)}
    assert set(GOOD) == set(S.REQUIRED_CONDS)


@pytest.mark.parametrize("scope", sorted(S.REQUIRED_CONDS))
def test_missing_condition_value_is_denied_even_when_a_grant_covers_everything(scope):
    g = _full_grant()
    res = _resource(scope)
    ok = engine.decide(P.peer("t"), scope, res, GOOD[scope], snap([g]))
    assert ok.allow, (scope, ok.reason)
    for ctx in ({}, {"mode": ""}, {"unrelated": "x"}):
        d = engine.decide(P.peer("t"), scope, res, ctx, snap([g]))
        assert (d.allow, d.reason) == (False, "cond_missing"), (scope, ctx)
    # 사람·기계 토큰 shim 의 「전부」 규칙도 값이 없으면 거부한다
    d = engine.decide(P.human("d", elevated=True), scope, res, {}, snap())
    assert not d.allow and d.reason in ("cond_missing",)


def test_scopes_without_required_conds_do_not_need_ctx():
    for scope in sorted(S.CATALOG - set(S.REQUIRED_CONDS)):
        assert S.missing_conds(scope, {}) == []


def test_unknown_condition_values_are_denied():
    g = _full_grant()
    assert not engine.decide(P.peer("t"), "session.input", "session:local/t", {"mode": "telepathy"}, snap([g])).allow
    assert not engine.decide(P.peer("t"), "relay", "relay:local/x", {"group": "nonexistent"}, snap([g])).allow
    assert not engine.decide(P.peer("t"), "session.input", "session:local/t", {"mode": "line", "via": "pigeon"}, snap([g])).allow


def test_grant_without_conds_uses_least_privilege_defaults():
    def allowed(scope, ctx, resource=None):
        g = {"id": "g", "subject": "peer:t", "resource": "*", "scopes": [scope], "schema": 2, "state": "active"}
        return engine.decide(P.peer("t"), scope, resource or _resource(scope), ctx, snap([g])).allow
    assert allowed("session.input", {"mode": "line"}) and not allowed("session.input", {"mode": "raw"})
    assert not allowed("session.input", {"mode": "line", "via": "queue"})
    assert not any(allowed("session.manage", {"op": o}) for o in S.MANAGE_OPS)
    assert not any(allowed("relay", {"group": g}) for g in S.ALL_RELAY_GROUPS) and not allowed("relay", {"group": "*"})
    assert allowed("fs.write", {"op": "write"}) and not allowed("fs.write", {"op": "commit"}) \
        and not allowed("fs.write", {"op": "drop"})
    assert all(allowed("browser", {"op": o}) for o in S.BROWSER_OPS)
    assert not any(allowed("browser.sensitive", {"op": o}) for o in S.SENSITIVE_OPS)
    assert not any(allowed("deleg.control", {"role": r}) for r in S.DELEG_ROLES)


def test_every_call_site_passes_the_required_condition_keys():
    """정적 검사 — authz 패키지의 `decide`/`shadow_check` 호출 중 scope 를 문자열로 적은 곳이 필요한 조건 키를 ctx 에 넣는다.
    scope 를 변수로 받는 곳(share_svc 등)은 authz_char 의 동적 검사(`_required_conds_spy`)가 본다."""
    root = pathlib.Path(S.__file__).parent
    checked = 0
    for f in sorted(root.glob("*.py")):
        if f.name in ("scopes.py", "engine.py", "shadow.py"):
            continue
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            assigned: dict[str, set] = {}
            for node in ast.walk(fn):
                if isinstance(node, ast.Assign) and len(node.targets) == 1:
                    t = node.targets[0]
                    if isinstance(t, ast.Name) and isinstance(node.value, ast.Dict):
                        assigned.setdefault(t.id, set()).update(k.value for k in node.value.keys if isinstance(k, ast.Constant))
                    if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name) and isinstance(t.slice, ast.Constant):
                        assigned.setdefault(t.value.id, set()).add(t.slice.value)
            for call in [n for n in ast.walk(fn) if isinstance(n, ast.Call)]:
                name = call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", "")
                if name not in ("decide", "shadow_check", "_decide"):
                    continue
                args = call.args
                lits = [(i, a.value) for i, a in enumerate(args) if isinstance(a, ast.Constant) and isinstance(a.value, str)
                        and a.value in S.REQUIRED_CONDS]
                if not lits:
                    continue
                i, scope = lits[0]
                ctx_i = i + 2 if name != "shadow_check" else i + 2
                ctx = args[ctx_i] if len(args) > ctx_i else next((k.value for k in call.keywords if k.arg == "ctx"), None)
                if isinstance(ctx, ast.IfExp):
                    ctx = ctx.body
                keys: set = set()
                if isinstance(ctx, ast.Dict):
                    keys = {k.value for k in ctx.keys if isinstance(k, ast.Constant)}
                elif isinstance(ctx, ast.Name):
                    keys = assigned.get(ctx.id, set())
                missing = set(S.REQUIRED_CONDS[scope]) - keys
                assert not missing, f"{f.name}:{call.lineno} {scope} ctx 에 {sorted(missing)} 가 없다"
                checked += 1
    assert checked >= 6


# --- elevated: (scope, 조건) 으로 본다 -----------------------------------------------------------------


@pytest.mark.parametrize("kind,scopes,sconds,resource,expect", [
    ("human", ["session.input", "browser.js", "browser.sensitive"], {"session.input": {"modes": ["raw"]}}, "", set()),
    ("agent", ["session.input"], None, "", set()),
    ("agent", ["session.input"], {"session.input": {"modes": ["line"]}}, "", set()),
    ("agent", ["session.input"], {"session.input": {"modes": ["raw"]}}, "", {"session.input[raw]"}),
    ("agent", ["session.input"], {"session.input": {"modes": ["line", "raw"]}}, "", {"session.input[raw]"}),
    ("agent", ["session.manage"], {"session.manage": {"ops": list(S.MANAGE_OPS)}}, "", set()),
    ("agent", ["relay"], {"relay": {"groups": ["sessions", "status"]}}, "", set()),
    ("agent", ["relay"], {"relay": {"groups": ["files.write"]}}, "", {"relay[files.write]"}),
    ("agent", ["relay"], {"relay": {"groups": ["git.write", "queue", "ws.term", "git"]}}, "",
     {"relay[git.write]", "relay[queue]", "relay[ws.term]"}),
    ("agent", ["relay"], {"relay": {"groups": ["*"]}}, "",
     {"relay[files.write]", "relay[git.write]", "relay[queue]", "relay[ws.term]"}),
    ("agent", ["relay"], None, "", set()),
    ("agent", ["browser"], {"browser": {"ops": list(S.BROWSER_OPS)}}, "", set()),
    ("agent", ["browser.js"], None, "", {"browser.js"}),
    ("agent", ["browser.sensitive"], {"browser.sensitive": {"ops": ["network"]}}, "", {"browser.sensitive"}),
    ("agent", ["browser.sensitive"], {"browser.sensitive": {"ops": ["download"]}}, "", {"browser.sensitive"}),
    ("agent", ["browser.sensitive"], None, "", {"browser.sensitive"}),
    ("peer", ["session.view", "session.read"], None, "", set()),
    ("peer", ["session.view", "session.read", "msg.send"], None, "", {"msg.send"}),
    ("peer", ["session.manage"], {"session.manage": {"ops": ["detach"]}}, "", {"session.manage"}),
    ("peer", ["session.input"], {"session.input": {"modes": ["raw"]}}, "", {"session.input", "session.input[raw]"}),
    ("agent", ["deleg.spawn"], None, "deleg-quota:agent:local/$1:1", set()),
    ("agent", ["deleg.spawn"], None, "deleg-quota:peer:m5", {"deleg.spawn(remote)"}),
    ("agent", ["fs.write"], {"fs.write": {"ops": ["commit"]}}, "", set()),
])
def test_elevated_comes_from_scope_and_conds(kind, scopes, sconds, resource, expect):
    assert S.requires_elevated(kind, scopes, resource, sconds) == expect


def test_escalation_compares_conds_for_every_new_shape():
    base = {"subject": "agent:local/a", "resource": "session:local/b", "scopes": ["session.input", "relay", "fs.write"],
            "sconds": {"session.input": {"modes": ["line"]}, "relay": {"groups": ["sessions"]},
                       "fs.write": {"ops": ["write"]}}}
    assert S.escalation(base, dict(base)) == []
    wide_modes = {**base, "sconds": {**base["sconds"], "session.input": {"modes": ["line", "raw"]}}}
    assert S.escalation(base, wide_modes) == ["cond_widened:session.input.modes"] and S.escalation(wide_modes, base) == []
    wide_groups = {**base, "sconds": {**base["sconds"], "relay": {"groups": ["sessions", "queue"]}}}
    assert S.escalation(base, wide_groups) == ["cond_widened:relay.groups"] and S.escalation(wide_groups, base) == []
    wide_ops = {**base, "sconds": {**base["sconds"], "fs.write": {"ops": ["write", "commit"]}}}
    assert S.escalation(base, wide_ops) == ["cond_widened:fs.write.ops"]
    # 조건을 지우면 기본값으로 돌아가는데, 기본값이 더 좁으면 확대가 아니고 넓으면 확대다
    no_conds = {k: v for k, v in base.items() if k != "sconds"}
    assert S.escalation(base, no_conds) == []          # 기본값(modes=[line], ops=[write], groups=[])은 base 보다 좁거나 같다
    assert S.escalation(no_conds, base) == ["cond_widened:relay.groups"]
    new_scope = {**base, "scopes": base["scopes"] + ["session.manage"]}
    assert S.escalation(base, new_scope) == ["scopes:session.manage"]


# --- 프리셋 -------------------------------------------------------------------------------------


def _view(name):
    sc, cd = S.preset(name)
    return S.caps_view(S.effective(sc, cd))


def test_presets_expand_to_the_right_scopes_and_conds():
    view = _view("view")
    assert view == {"session.view": {}, "session.read": {}}
    collab = _view("collab")
    assert set(collab) == {"session.view", "session.read", "msg.send", "fs.read", "browser"}
    assert collab["browser"] == {"ops": ["read"]}
    drive = _view("drive")
    assert set(drive) == set(collab) | {"session.input", "fs.write", "deleg.spawn"}
    assert drive["session.input"] == {"modes": ["line"], "via": ["direct"]} and drive["fs.write"] == {"ops": ["write"]}
    assert drive["browser"] == {"ops": sorted(S.BROWSER_OPS)}
    control = _view("control")
    assert set(control) == set(drive) | {"session.manage", "relay"}
    assert control["session.input"]["modes"] == ["line", "raw"] and control["session.manage"] == {"ops": sorted(S.MANAGE_OPS)}
    assert control["relay"] == {"groups": list(S.ALL_RELAY_GROUPS)}
    # 위로 갈수록 부분집합이다
    caps = [S.effective(*S.preset(n)) for n in S.PRESET_ORDER]
    for lo, hi in zip(caps, caps[1:]):
        assert S.caps_subset(lo, hi)
    assert [S.preset_requires_elevated(n) for n in S.PRESET_ORDER[:3]] == [set(), set(), set()]
    assert S.preset_requires_elevated("control") == {"session.input[raw]", "relay[files.write]", "relay[git.write]",
                                                      "relay[queue]", "relay[ws.term]"}
    assert S.PRESET_LABELS == {"view": "보기", "collab": "협업", "drive": "운전", "control": "제어"}


def test_peer_presets_are_what_the_old_levels_allowed_and_nothing_more():
    """hosts.json 의 view/control 이전은 옛 등급에 있던 것만 준다 — 「제어」 프리셋의 msg·fs.read·browser·spawn 은 포함하지 않는다."""
    vs, vc = defaults.peer_preset("view")
    assert S.effective(vs, vc) == S.effective(*S.preset("view"))
    cs, cc = defaults.peer_preset("control")
    ctl = S.effective(cs, cc)
    assert S.effective(cs, cc)["fs.write"] == {"ops": frozenset({"drop"})}      # 옛 control 은 파일 drop 만 가졌다
    assert S.caps_subset({k: v for k, v in ctl.items() if k != "fs.write"}, S.effective(*S.preset("control")))
    assert not {"msg.send", "fs.read", "browser", "deleg.spawn"} & set(ctl)
    assert S.effective(cs, cc)["relay"]["groups"] == frozenset(S.ALL_RELAY_GROUPS)


def _old_peer_allow(level_names, name):
    return name in S.legacy_expand(level_names) or name == "ping"


@pytest.mark.parametrize("enforce", ["", "share", "deleg", "msg", "share,deleg,msg"])
@pytest.mark.parametrize("level,agent_messages", [("view", False), ("control", False), ("control", True), ("view", True)])
def test_peer_grant_split_gives_the_same_decisions_for_every_level_and_flag(enforce, level, agent_messages, monkeypatch):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", enforce)
    old = OLD_PEER_CONTROL if level == "control" else OLD_PEER_VIEW
    g = {"id": "pp", "level": level, "agentMessages": agent_messages}
    s = snap(migrate.translate_peer_grant(g))
    plain, via_agent = P.peer("pp"), P.peer("pp", obo="agent:pp/-")
    for name in OLD_67:
        scope, ctx = S.coarse_call(name) if S.LEGACY_TO_COARSE[name][0] not in (None, "internal") else (name, {})
        if name == "msg.read":
            continue
        if name in ("ping", "api.access"):
            got = engine.decide(plain, name, "host:local", {}, s).allow
            assert got is (name == "ping"), name
            continue
        res = "relay:local/x" if scope == "relay" else _resource(scope)
        if name == "msg.send":
            exp_plain, exp_obo = False, agent_messages          # obo 없이는 안 되고, agentMessages 일 때만 대리 주체가 된다
            assert engine.decide(plain, scope, res, ctx, s).allow is exp_plain
            assert engine.decide(via_agent, scope, res, ctx, s).allow is exp_obo
            continue
        have = S.legacy_expand(old)
        exp = any(x in have for x in _merged(name)) or (name == "relay.connect" and level == "control")
        assert engine.decide(plain, scope, res, ctx, s).allow is exp, (level, name)
        if name != "relay.connect":
            assert exp is _old_peer_allow(old, name) or len(_merged(name)) > 1


def test_agent_default_is_own_session_view_read_and_line_input_only():
    a = P.agent("$1:1")
    own = "session:local/$1:1"
    assert engine.decide(a, "session.view", own, {}).allow and engine.decide(a, "session.read", own, {}).allow
    assert engine.decide(a, "session.input", own, {"mode": "line"}).allow
    assert not engine.decide(a, "session.input", own, {"mode": "raw"}).allow
    assert not engine.decide(a, "session.input", own, {"mode": "line", "via": "queue"}).allow
    for op in S.MANAGE_OPS:
        assert not engine.decide(a, "session.manage", own, {"op": op}).allow
    assert not engine.decide(a, "session.read", "session:local/$2:2", {}).allow
    assert engine.decide(a, "agent.report", own, {}).allow and engine.decide(a, "authz.request", "authz:*", {}).allow
    # 자기 기록 읽기(msg.read)는 scope 가 아니다 — 판정을 거치지 않는다
    assert engine.decide(a, "msg.read", "msg:local/$1:1", {}).reason == "unknown_scope"


def test_human_default_changes_are_limited_to_the_documented_tightening():
    """옛 human 기본(67개 중 relay·js·cookies·attach·(비elevated)admin 제외) 대비: 달라지는 곳은 browser.sensitive 의 나머지 ops
    (storage·network·download) 와 비elevated 의 authz.audit(admin 으로 합쳐짐) 뿐이다 — 둘 다 지금은 닿지 않는 경로다."""
    old_ok = {n for n in OLD_67 if not n.startswith("relay.") and n not in ("browser.js", "browser.cookies", "browser.attach",
                                                                           "msg.read")}
    for elevated in (False, True):
        h = P.human("d", elevated=elevated)
        old = set(old_ok) - (set() if elevated else {"authz.admin"})
        new = set()
        for n in OLD_67:
            scope, ctx = S.coarse_call(n) if S.LEGACY_TO_COARSE[n][0] not in (None, "internal") else (n, {})
            if n == "msg.read":
                continue
            if engine.decide(h, scope, _resource(scope) if scope in S.CATALOG else "host:local", ctx, snap()).allow:
                new.add(n)
        expect = {"browser.storage", "browser.network", "browser.download"} | (set() if elevated else {"authz.audit"})
        assert old - new == expect, (elevated, old - new)
        assert new <= old, new - old                 # 넓어진 곳은 없다


# --- 주체 종류 5개 -----------------------------------------------------------------------------------


def test_principal_kinds_are_five_and_pair_anon_are_not_principals():
    assert P.KINDS == ("human", "local", "agent", "peer", "legacy_token")
    for gone in ("PAIR", "ANON", "CLI", "SVC", "HOOK"):
        assert not hasattr(P, gone), gone
    assert not hasattr(P, "anon")


def test_local_merges_cli_and_svc_with_a_name_attribute():
    c = P.cli(501, 42)
    assert (c.kind, c.id, c.attrs["name"]) == ("local", "local:cli:uid501:pid42", "cli") and P.is_cli(c) and not P.is_svc(c)
    s = P.svc("clipboard", 501, 7)
    assert (s.kind, s.id, s.attrs["name"], s.attrs["svc"]) == ("local", "local:svc:clipboard", "svc:clipboard", "clipboard")
    assert P.is_svc(s) and not P.is_cli(s) and not s.elevated
    # 이름은 귀속용 — 어느 쪽도 human 이 아니고, 기본 권한은 cli 의 위임 닫기뿐이다
    assert engine.decide(c, "deleg.control", "deleg:local/d", {"role": "parent", "op": "close"}).allow
    assert not engine.decide(c, "deleg.control", "deleg:local/d", {"role": "parent", "op": "send"}).allow
    assert not engine.decide(c, "session.view", "session:local/x", {}).allow
    assert not engine.decide(s, "deleg.control", "deleg:local/d", {"role": "parent", "op": "close"}).allow


def test_hook_from_an_agent_pane_stays_an_agent_with_via_hook():
    class Row:
        sid, created, name, pane_id = "$1", 111, "main", "%1"
    hook = P.from_socket({"uid": 501, "pid": 9}, row=Row(), svc_claim="hook")
    plain = P.from_socket({"uid": 501, "pid": 9}, row=Row())
    assert (hook.kind, hook.id) == ("agent", "agent:local/$1:111") and hook.id == plain.id
    assert P.is_hook(hook) and not P.is_hook(plain) and hook.attrs["via"] == "hook" and plain.attrs["via"] == "socket"
    # hook 이어도 같은 agent 의 자기 세션 권한이다
    assert engine.decide(hook, "agent.report", "session:local/$1:111", {}).allow
    out_of_pane = P.from_socket({"uid": 501, "pid": 9}, row=None, svc_claim="hook")
    assert out_of_pane.kind == "local" and P.is_svc(out_of_pane) and out_of_pane.id == "local:svc:hook"


def test_store_corruption_fails_closed_for_cli_but_not_for_svc_or_human():
    corrupt = snap(corrupt={"grants"})
    # 정적 기본 규칙(위임 닫기)은 저장소와 무관하게 동작하고, 저장소에서 와야 하는 것은 cli 만 닫힌다
    assert engine.decide(P.cli(1, 2), "deleg.control", "deleg:local/d", {"role": "parent", "op": "close"}, corrupt).allow
    assert engine.decide(P.cli(1, 2), "session.view", "session:local/x", {}, corrupt).reason == "store_corrupt"
    assert engine.decide(P.svc("tui", 1, 2), "session.view", "session:local/x", {}, corrupt).reason == "no_grant"
    assert engine.decide(P.human("d"), "session.view", "session:local/x", {}, corrupt).allow


def test_audit_identity_strings():
    assert P.human("dev1").id == "human:dev:dev1"
    assert P.cli(501, 42).id == "local:cli:uid501:pid42"
    assert P.svc("voice", 501, 1).id == "local:svc:voice"
    assert P.agent("$1:111").id == "agent:local/$1:111"
    assert P.peer("m5").id == "peer:m5" and P.peer("m5", obo="agent:m5/x").short() == "peer:m5/obo:agent:m5/x"
    assert P.legacy_token().id == "legacy_token" and P.open_server().id == "legacy:open"
    assert P.unauthenticated().id == "unauthenticated" and not P.unauthenticated().authenticated
    migrate.apply(tmux={})
    rows = [json.loads(x) for x in audit.path().read_text().splitlines()]
    assert {r["principal"] for r in rows if r["action"].startswith("authz.migrate")} == {"local:cli:migrate-auth"}


# --- 스키마 2: 읽기 변환, 쓰기, 백업·복원 왕복 ---------------------------------------------------------


def _old_store():
    """옛 이름(67개) 으로 저장된 authz 저장소 — v1 문서를 손으로 만든다."""
    d = store.root()
    d.mkdir(parents=True, exist_ok=True)
    grants = {"version": 1, "items": [
        {"id": "ag-1", "origin": "native:g1", "subject": "agent:local/$1:1", "resource": "session:local/$2:2",
         "scopes": ["session.list", "session.read", "session.input.line"], "state": "active", "conds": {},
         "subject_session": "$1:1", "resource_session": "$2:2", "approved_by": "human:dev:d", "expires": None},
        {"id": "pg-p", "origin": "native:pg", "subject": "peer:p", "resource": "*", "state": "active", "conds": {},
         "scopes": ["session.list", "session.meta", "session.read", "session.stream", "session.clients.view", "ping",
                    "session.input.raw", "session.create", "session.kill", "session.rename", "session.clients.detach",
                    "fs.drop", "relay.*"], "preset": "control", "approved_by": "human:dev:d", "expires": None},
        {"id": "x-deny", "effect": "deny", "subject": "agent:*", "resource": "session:local/$9:9", "scopes": ["session.*"]},
    ]}
    approvals = {"version": 1, "items": [
        {"id": "ap-1", "origin": "native:req1", "subject": "agent:local/$1:1", "resource": "session:local/$2:2",
         "scopes": ["session.list", "session.read", "session.input.line"], "state": "pending", "expires": NOW + 999,
         "subject_session": "$1:1", "resource_session": "$2:2"}]}
    (d / "grants.json").write_text(json.dumps(grants, indent=1))
    (d / "approvals.json").write_text(json.dumps(approvals, indent=1))
    return grants, approvals


def _decisions(sn):
    a, p = P.agent("$1:1"), P.peer("p")
    res = "session:local/$2:2"
    ctx = {"share": {"nick": "x", "mode": "readwrite"}, "alive": None}
    out = {
        "read": engine.decide(a, "session.read", res, ctx, sn).allow,
        "line": engine.decide(a, "session.input", res, {**ctx, "mode": "line"}, sn).allow,
        "raw": engine.decide(a, "session.input", res, {**ctx, "mode": "raw"}, sn).allow,
        "peer_stream": engine.decide(p, "session.read", "session:local/q", {}, sn).allow,
        "peer_raw": engine.decide(p, "session.input", "session:local/q", {"mode": "raw"}, sn).allow,
        "peer_kill": engine.decide(p, "session.manage", "session:local/q", {"op": "kill"}, sn).allow,
        "peer_drop": engine.decide(p, "fs.write", "fs:local/q", {"op": "drop"}, sn).allow,
        "peer_write": engine.decide(p, "fs.write", "fs:local/q", {"op": "write"}, sn).allow,
        "peer_relay": engine.decide(p, "relay", "relay:local/queue", {"group": "queue"}, sn).allow,
        "peer_gate": engine.decide(p, "relay", "relay:local", {"group": "*"}, sn).allow,
        "denied": engine.decide(P.agent("$1:1"), "session.read", "session:local/$9:9", {}, sn).reason,
    }
    return out


def test_old_name_store_reads_translated_and_decides_the_same():
    _old_store()
    sn = store.snapshot(NOW)
    g = {x["id"]: x for x in sn.grants}
    assert g["ag-1"]["scopes"] == ["session.view", "session.read", "session.input"] and g["ag-1"]["schema"] == 2
    assert g["ag-1"]["sconds"] == {"session.input": {"modes": ["line"]}}
    assert g["pg-p"]["sconds"]["relay"]["groups"] == list(S.ALL_RELAY_GROUPS) and "ping" not in g["pg-p"]["scopes"]
    assert "session.manage" in g["x-deny"]["scopes"]
    assert store.schema_report()["grants"] == {"version": 1, "items": 3, "legacy_items": 3}
    d = _decisions(sn)
    assert d == {"read": True, "line": True, "raw": False, "peer_stream": True, "peer_raw": True, "peer_kill": True,
                 "peer_drop": True, "peer_write": False, "peer_relay": True, "peer_gate": True, "denied": "no_grant"}
    # 금지 규칙은 조건이 없으면 넓게 막는다
    deny = {"id": "dd", "effect": "deny", "subject": "agent:*", "resource": "session:local/$2:2", "scopes": ["session.input"],
            "schema": 2}
    sd = store.Snapshot(grants=sn.grants + [deny], now=NOW)
    assert engine.decide(P.agent("$1:1"), "session.input", "session:local/$2:2",
                         {"mode": "line", "share": {"nick": "x", "mode": "readwrite"}}, sd).rule == "deny"


def test_writing_converts_to_schema_2_after_an_automatic_pre_schema2_backup():
    _old_store()
    before = {n: (store.root() / f"{n}.json").read_bytes() for n in ("grants", "approvals")}
    with store.txn() as docs:
        docs["grants"]["items"].append({"id": "extra", "subject": "peer:z", "resource": "*", "scopes": ["ping", "session.list"]})
    raw = json.loads((store.root() / "grants.json").read_text())
    assert raw["version"] == 2 and all(x["schema"] == 2 for x in raw["items"])
    assert next(x for x in raw["items"] if x["id"] == "ag-1")["scopes"] == ["session.view", "session.read", "session.input"]
    assert next(x for x in raw["items"] if x["id"] == "extra")["scopes"] == ["session.view"]
    # 쓰지 않은 문서는 그대로(읽을 때만 변환)
    assert (store.root() / "approvals.json").read_bytes() == before["approvals"]
    backups = migrate.list_backups()
    assert len(backups) == 1 and backups[0]["ts"].endswith("-pre-schema2")
    assert store.schema_report()["grants"]["legacy_items"] == 0 and store.schema_report()["approvals"]["legacy_items"] == 1


def test_backup_restore_round_trip_returns_the_old_names_byte_for_byte_with_identical_decisions():
    _old_store()
    before = {n: (store.root() / f"{n}.json").read_bytes() for n in ("grants", "approvals")}
    d0 = _decisions(store.snapshot(NOW))
    bk = migrate.backup(tmux=None)
    res = migrate.apply(tmux={})                       # 새 형태로 전부 다시 쓴다
    assert res["backup"]
    raw = json.loads((store.root() / "grants.json").read_text())
    assert raw["version"] == 2 and all(x["schema"] == 2 for x in raw["items"]), "apply 가 문서를 새 형태로 쓴다"
    assert store.schema_report()["approvals"]["legacy_items"] == 0
    d1 = _decisions(store.snapshot(NOW))
    assert d1 == d0                                    # 새 형태에서도 판정이 같다
    out = migrate.restore(bk["ts"])
    assert "authz/grants.json" in out["restored"]
    after = {n: (store.root() / f"{n}.json").read_bytes() for n in ("grants", "approvals")}
    assert after == before                             # 옛 이름 문서가 바이트 단위로 돌아온다
    assert store.schema_report()["grants"]["legacy_items"] == 3
    assert _decisions(store.snapshot(NOW)) == d0


def test_status_and_verify_report_the_schema(capsys):
    _old_store()
    assert cli.main(["--status"]) == 0
    out = capsys.readouterr().out
    assert "스키마 grants: version=1 항목 3개, 옛 이름 형태 3개" in out and "코드 스키마 2" in out
    st = migrate.status()
    assert st["schema_version"] == 2 and st["schema"]["approvals"]["legacy_items"] == 1


def test_audit_history_is_never_rewritten_by_the_schema_change():
    audit.append({"principal": "agent:local/$1:1", "action": "share.read", "resource": "session:local/$2:2",
                  "scopes": ["session.list", "session.input.line"], "decision": "allow"})
    first = audit.path().read_text()
    _old_store()
    with store.txn() as docs:
        docs["grants"]["items"].append({"id": "n", "scopes": ["session.read"]})
    migrate.apply(tmux={})
    text = audit.path().read_text()
    assert text.startswith(first)                      # 옛 이름이 적힌 과거 줄은 그대로
    assert "session.input.line" in first and audit.verify()[0] is True


def test_agent_grant_dual_write_shape_is_unchanged_for_the_old_file(tmp_path):
    """엔진 레코드 -> 옛 모양 -> 다시 번역하면 같은 레코드(왕복) — 옛 agent_grants.json 은 새 형태에서도 같은 모양으로 투영된다."""
    from authz import approvals
    legacy = {"id": "g1", "agent": {"sid": "$1", "created": 1, "name": "a"}, "target": {"sid": "$2", "created": 2},
              "read": True, "write": True, "granted": 5.0}
    rec = approvals.engine_grant(legacy, approved_by="human:dev:d")
    assert rec["scopes"] == ["session.view", "session.read", "session.input"] and rec["schema"] == 2
    back = approvals.grant_to_legacy(rec)
    assert back["read"] is True and back["write"] is True and back["id"] == "g1"
    ro = approvals.engine_grant({**legacy, "write": False}, approved_by="x")
    assert approvals.grant_to_legacy(ro)["write"] is False and approvals.kind_of(ro) == "read"
    assert approvals.kind_of(rec) == "write"
    assert hashlib.sha256(json.dumps(rec, sort_keys=True).encode()).hexdigest()   # 직렬화 가능
