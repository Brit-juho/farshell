"""code_fingerprint.py — 서버 코드의 "실행에 영향 주는 내용" 지문.

`fsh status` / `fsh doctor`는 서버가 옛 코드로 돌고 있는지를 파일 수정 시각으로 판단했다.
그런데 주석·docstring·공백만 바꿔도 수정 시각이 바뀌어, 실제로는 같은 코드인데 "옛 코드로
돌고 있습니다. 재시작하세요"가 떴다(2026-10-02, `agents.py` 주석 수정).

서버를 띄울 때 이 지문을 저장하고, 나중에 수정 시각이 더 최근인 파일이 있으면 지문을 다시
계산해 비교한다. 지문은 각 파일의 **AST**(주석·공백·docstring 제외)로 만들므로 같으면 재시작이
필요 없다. 파싱이 안 되는 파일은 원문 바이트로 해시해서 변경으로 취급한다.

CLI: python -m code_fingerprint [서버 디렉터리]   → 지문 한 줄 출력
"""

from __future__ import annotations

import ast
import hashlib
import os
import sys

_SKIP_DIRS = {"__pycache__", "tests", ".venv"}


def _strip_docstrings(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                # 본문이 docstring 하나뿐이면 pass로 대체해 구문이 유지되게 한다
                node.body = body[1:] or [ast.Pass()]


def _file_digest(path: str) -> bytes:
    with open(path, "rb") as f:
        raw = f.read()
    try:
        tree = ast.parse(raw.decode("utf-8"), filename=path)
        _strip_docstrings(tree)
        return ast.dump(tree, include_attributes=False).encode("utf-8")
    except (SyntaxError, UnicodeDecodeError, ValueError):
        return b"RAW:" + raw


def fingerprint(server_dir: str) -> str:
    h = hashlib.sha256()
    entries: list[str] = []
    for root, dirs, files in os.walk(server_dir):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
        for name in sorted(files):
            if name.endswith(".py"):
                entries.append(os.path.join(root, name))
    for path in entries:
        h.update(os.path.relpath(path, server_dir).encode("utf-8"))
        h.update(b"\0")
        try:
            h.update(_file_digest(path))
        except OSError:
            h.update(b"MISSING")
        h.update(b"\0")
    return h.hexdigest()


if __name__ == "__main__":
    print(fingerprint(sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))))
