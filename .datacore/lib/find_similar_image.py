#!/usr/bin/env python3
"""Find visually similar images on disk or inside presentation files.

Useful for locating a lost graphic: give it a reference image, and it ranks
candidates by perceptual similarity (32x32 grayscale, mean-centred cosine),
so rescaled/re-encoded copies still match.

Usage:
    python3 find_similar_image.py REFERENCE.png [PATH ...]           # scan image files
    python3 find_similar_image.py REFERENCE.png --decks [PATH ...]   # scan inside .pptx/.key

Defaults to scanning ~/Data when no PATH is given.
"""
import os
import sys
import warnings
import zipfile

warnings.filterwarnings("ignore")
from PIL import Image  # noqa: E402

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".tiff", ".bmp", ".webp")
DECK_EXTS = (".pptx", ".key", ".odp")
SKIP_DIRS = {"node_modules", ".git", "Library", ".Trash", "__pycache__"}


def signature(im):
    im = im.convert("L").resize((32, 32), Image.LANCZOS)
    px = list(im.getdata())
    mean = sum(px) / len(px)
    return [p - mean for p in px]


def similarity(a, b):
    num = sum(x * y for x, y in zip(a, b))
    da = sum(x * x for x in a) ** 0.5
    db = sum(y * y for y in b) ** 0.5
    return num / (da * db) if da and db else 0.0


def walk(roots, exts):
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in filenames:
                if name.lower().endswith(exts):
                    yield os.path.join(dirpath, name)


def scan_files(target, roots, min_width):
    for path in walk(roots, IMAGE_EXTS):
        try:
            im = Image.open(path)
            if im.width < min_width:
                continue
            im.load()
            yield similarity(target, signature(im)), im.width, im.height, path
        except Exception:
            continue


def scan_decks(target, roots, min_width):
    for deck in walk(roots, DECK_EXTS):
        if not os.path.isfile(deck):
            continue
        try:
            archive = zipfile.ZipFile(deck)
        except Exception:
            continue
        for entry in archive.namelist():
            if not entry.lower().endswith(IMAGE_EXTS):
                continue
            try:
                with archive.open(entry) as fh:
                    im = Image.open(fh)
                    im.load()
                if im.width < min_width:
                    continue
                yield similarity(target, signature(im)), im.width, im.height, f"{deck}::{entry}"
            except Exception:
                continue


def main():
    args = [a for a in sys.argv[1:]]
    if not args:
        print(__doc__)
        return 1
    reference = args.pop(0)
    decks_mode = "--decks" in args
    args = [a for a in args if not a.startswith("--")]
    roots = args or [os.path.expanduser("~/Data")]

    target = signature(Image.open(reference))
    scanner = scan_decks if decks_mode else scan_files
    rows = sorted(scanner(target, roots, min_width=200), reverse=True)

    if not rows:
        print("No candidates found.")
        return 0
    for score, w, h, path in rows[:20]:
        print(f"{score:.4f}  {w}x{h}  {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
