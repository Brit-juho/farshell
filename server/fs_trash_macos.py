"""macOS 휴지통 도우미 — `fs_trash`가 **별도 프로세스**로 부른다(`python -I fs_trash_macos.py <절대경로>`).

`NSFileManager.trashItemAtURL:resultingItemURL:error:`를 ctypes로 부른다. Finder와 같은 길이라 휴지통의 `.DS_Store`에
원래 위치(`ptbL`)와 이름(`ptbN`)이 남고, Finder의 「되돌리기」(Put Back)가 동작한다. 같은 이름이 이미 휴지통에 있으면
시스템이 `이름 16-10-17-901.txt`처럼 바꾼다. 휴지통이 없는 볼륨·권한 없음·읽기 전용이면 오류를 돌려준다 —
지우는 길(unlink)로 넘어가지 않는다.

별도 프로세스인 이유: Objective-C 호출이 잘못되면 프로세스가 통째로 죽는다(JXA `osascript`로 같은 호출을 하면
out 인자에서 실제로 SIGSEGV가 났다). 서버 프로세스 안에서 부르지 않는다. Finder를 AppleScript로 부르는 길은
자동화 권한(TCC) 창을 띄우고 그 답을 기다리며 멈출 수 있어 쓰지 않는다.

출력: 표준출력 한 줄 JSON — `{"ok": true, "path": "<휴지통 안 경로>"}` 또는 `{"ok": false, "code": n, "reason": "…"}`.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import json
import sys


def _trash(path: str) -> dict:
    objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
    ctypes.cdll.LoadLibrary(ctypes.util.find_library("Foundation"))
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]

    def msg(restype, *argtypes):
        return ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *argtypes)(("objc_msgSend", objc))

    def cls(name: bytes):
        return objc.objc_getClass(name)

    def sel(name: bytes):
        return objc.sel_registerName(name)

    obj0 = msg(ctypes.c_void_p)
    pool = obj0(obj0(cls(b"NSAutoreleasePool"), sel(b"alloc")), sel(b"init"))
    try:
        nsstr = msg(ctypes.c_void_p, ctypes.c_char_p)(cls(b"NSString"), sel(b"stringWithUTF8String:"), path.encode("utf-8"))
        if not nsstr:
            return {"ok": False, "code": -1, "reason": "bad path"}
        url = msg(ctypes.c_void_p, ctypes.c_void_p)(cls(b"NSURL"), sel(b"fileURLWithPath:"), nsstr)
        fm = obj0(cls(b"NSFileManager"), sel(b"defaultManager"))
        out = ctypes.c_void_p()
        err = ctypes.c_void_p()
        ok = msg(ctypes.c_bool, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p))(
            fm, sel(b"trashItemAtURL:resultingItemURL:error:"), url, ctypes.byref(out), ctypes.byref(err))
        utf8 = msg(ctypes.c_char_p)
        if ok and out.value:
            p = obj0(out, sel(b"path"))
            return {"ok": True, "path": utf8(p, sel(b"UTF8String")).decode("utf-8", "replace")}
        if err.value:
            code = msg(ctypes.c_long)(err, sel(b"code"))
            desc = obj0(err, sel(b"localizedDescription"))
            text = utf8(desc, sel(b"UTF8String")) if desc else b""
            return {"ok": False, "code": int(code), "reason": (text or b"").decode("utf-8", "replace")[:300]}
        return {"ok": False, "code": -1, "reason": "unknown"}
    finally:
        if pool:
            obj0(pool, sel(b"drain"))


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not argv[1].startswith("/"):
        print(json.dumps({"ok": False, "code": -1, "reason": "usage"}))
        return 2
    print(json.dumps(_trash(argv[1]), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
