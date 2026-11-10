#!/usr/bin/env python3
"""macos/AppIcon.icns 를 다시 만든다(산출물은 저장소에 커밋 — 설치 환경에 이미지 도구가 없어도 되도록).

필요한 도구: Pillow(저장소 .venv), macOS 의 `iconutil`. 원본 래스터를 늘리지 않고 frontend/icon-512.png 의 그림
(`>_`)을 같은 색·비율로 벡터처럼 직접 그려 각 크기에서 4배 슈퍼샘플링 후 줄인다.
규격: 1024 캔버스, 824x824 둥근 사각형(반경 185) 가운데, 바깥 투명.
사용: .venv/bin/python scripts/gen_app_icon.py [출력.icns]   (바꾸면 FarShell.app 이 다시 만들어져 권한을 한 번 다시 묻는다)
"""
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
BG, FG = (23, 24, 27, 255), (122, 162, 255, 255)   # icon-512.png 픽셀에서 읽은 값
SS = 4


def draw(size: int) -> Image.Image:
    n = size * SS
    k = n / 1024
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    box, rad = 824 * k, 185 * k
    o = (n - box) / 2
    d.rounded_rectangle([o, o, o + box, o + box], radius=rad, fill=BG)
    s = box / 512                      # 원본 512 좌표 -> 캔버스
    P = lambda x, y: (o + x * s, o + y * s)
    r = 24 * s                         # 획 폭 48 의 절반

    def stroke(pts):
        for a, b in zip(pts, pts[1:]):
            d.line([P(*a), P(*b)], fill=FG, width=round(2 * r))
        for p in pts:
            cx, cy = P(*p)
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=FG)

    stroke([(128, 144), (222, 256), (128, 368)])
    stroke([(256, 368), (384, 368)])
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "macos" / "AppIcon.icns"
    with tempfile.TemporaryDirectory() as t:
        iset = Path(t) / "AppIcon.iconset"
        iset.mkdir()
        for base in (16, 32, 128, 256, 512):
            draw(base).save(iset / f"icon_{base}x{base}.png")
            draw(base * 2).save(iset / f"icon_{base}x{base}@2x.png")
        subprocess.run(["iconutil", "-c", "icns", str(iset), "-o", str(out)], check=True)
    draw(1024).save("/tmp/farshell-icon-1024.png")
    draw(128).save("/tmp/farshell-icon-128.png")


if __name__ == "__main__":
    main()
