"""code_fingerprint — 주석·docstring만 바뀐 것은 같은 코드로, 로직이 바뀐 것은 다른 코드로 본다."""

import code_fingerprint as cf


def _mk(tmp_path, text, name="mod.py"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


BASE = '''"""모듈 설명."""
import os


def f(x):
    """옛 설명."""
    # 옛 주석
    return x + 1
'''


def test_comment_docstring_whitespace_changes_keep_fingerprint(tmp_path):
    p = _mk(tmp_path, BASE)
    before = cf.fingerprint(str(tmp_path))
    p.write_text(BASE.replace("옛 설명", "새 설명").replace("옛 주석", "새 주석 — 더 길게")
                 .replace("return x + 1", "return  x + 1"), encoding="utf-8")
    assert cf.fingerprint(str(tmp_path)) == before


def test_logic_change_changes_fingerprint(tmp_path):
    p = _mk(tmp_path, BASE)
    before = cf.fingerprint(str(tmp_path))
    p.write_text(BASE.replace("x + 1", "x + 2"), encoding="utf-8")
    assert cf.fingerprint(str(tmp_path)) != before


def test_new_or_removed_file_changes_fingerprint(tmp_path):
    _mk(tmp_path, BASE)
    base = cf.fingerprint(str(tmp_path))
    extra = _mk(tmp_path, "X = 1\n", "extra.py")
    assert cf.fingerprint(str(tmp_path)) != base
    extra.unlink()
    assert cf.fingerprint(str(tmp_path)) == base


def test_syntax_error_counts_as_change_even_if_only_comment_differs(tmp_path):
    p = _mk(tmp_path, "def broken(:\n    pass  # a\n")
    a = cf.fingerprint(str(tmp_path))
    p.write_text("def broken(:\n    pass  # b\n", encoding="utf-8")
    assert cf.fingerprint(str(tmp_path)) != a  # 파싱이 안 되면 원문으로 비교


def test_docstring_only_function_still_parses(tmp_path):
    _mk(tmp_path, 'def f():\n    """only doc"""\n')
    assert len(cf.fingerprint(str(tmp_path))) == 64


def test_tests_and_pycache_dirs_are_ignored(tmp_path):
    _mk(tmp_path, BASE)
    base = cf.fingerprint(str(tmp_path))
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "t.py").write_text("assert True\n", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "x.py").write_text("junk\n", encoding="utf-8")
    assert cf.fingerprint(str(tmp_path)) == base
