"""One-off: stitch three real save_to_disk screenshots from a live
BASTION session into an animated GIF for the landing page's "see it
work" embed. Not part of the app build -- run once, output committed as
a static asset (frontend/public/live-graph-demo.gif), this script isn't
itself referenced by anything at runtime.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

SRC_DIR = Path(
    r"C:\Users\cnnir\AppData\Local\Temp\claude-chrome-screenshots-Hd138j"
)
FRAMES = [
    "screenshot-1787834421095-0.jpg",  # inspector open, real span detail
    "screenshot-1787834471596-1.jpg",  # inspector closed, live graph
    "screenshot-1787834492543-2.jpg",  # two more real calls landed
]
OUT_PATH = Path(__file__).resolve().parents[1] / "public" / "live-graph-demo.gif"

TARGET_WIDTH = 960


def main() -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    images = []
    for name in FRAMES:
        img = Image.open(SRC_DIR / name).convert("RGB")
        ratio = TARGET_WIDTH / img.width
        img = img.resize((TARGET_WIDTH, int(img.height * ratio)), Image.LANCZOS)
        images.append(img)

    images[0].save(
        OUT_PATH,
        save_all=True,
        append_images=images[1:],
        duration=[1800, 1400, 2200],
        loop=0,
        optimize=True,
    )
    print(f"wrote {OUT_PATH} ({OUT_PATH.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
