"""Copy screenshots into the docs as 256-colour PNGs, about a quarter the size.

uv run --with pillow python quantize.py RAW_DIR DOCS_SCREENSHOTS_DIR
"""

import sys
from pathlib import Path

from PIL import Image


def quantize(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    image = Image.open(source).convert("RGB")
    image.quantize(
        colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE
    ).save(target, optimize=True)


def main(raw: Path, docs: Path) -> None:
    for source in sorted(raw.rglob("*.png")):
        target = docs / source.relative_to(raw)
        quantize(source, target)
        print(f"{target} {target.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
