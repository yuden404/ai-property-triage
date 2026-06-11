"""Build a balanced 6-class training set in data/ from public Kaggle datasets.

Combines two datasets (downloaded via kagglehub → no manual download):
  - robinreni/house-rooms-image-dataset      → kitchen / bathroom / bedroom /
                                                living_room, and Dinning → other
  - mikhailma/house-rooms-streets-image-dataset → street_data → exterior

Takes a fixed-seed balanced subsample (default 250/class) and copies it into
data/<class>/. Idempotent: clears each class folder (keeping .gitkeep) first.

Usage (from code/image_analyser/):
    ../../.venv/bin/python prepare_data.py --per-class 250
Needs Kaggle API creds at ~/.kaggle/kaggle.json (free token).
"""
from __future__ import annotations

import argparse
import os
import random
import shutil
from pathlib import Path

import kagglehub

HERE = Path(__file__).parent
DATA = HERE / "data"
SEED = 42
IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".bmp")

DATASETS = [
    "robinreni/house-rooms-image-dataset",
    "mikhailma/house-rooms-streets-image-dataset",
    "prasunroy/natural-images",   # diverse non-room negatives → not_a_room
]

# source folder basename (lowercased) -> our class label
FOLDER_MAP = {
    "kitchen": "kitchen",
    "bathroom": "bathroom",
    "bedroom": "bedroom",
    "livingroom": "living_room",
    "dinning": "other",   # dining room → the "other" bucket
    "dining": "other",
    "street_data": "exterior",
    # natural-images categories → a single "not_a_room" reject class
    "airplane": "not_a_room", "car": "not_a_room", "cat": "not_a_room",
    "dog": "not_a_room", "flower": "not_a_room", "fruit": "not_a_room",
    "motorbike": "not_a_room", "person": "not_a_room",
}


def collect_pools() -> dict[str, list[str]]:
    """Map every matching source folder to a pool of image paths per class."""
    pools: dict[str, list[str]] = {c: [] for c in set(FOLDER_MAP.values())}
    for slug in DATASETS:
        root = kagglehub.dataset_download(slug)
        for dirpath, _dirs, files in os.walk(root):
            cls = FOLDER_MAP.get(os.path.basename(dirpath).lower())
            if not cls:
                continue
            pools[cls] += [os.path.join(dirpath, f) for f in files if f.lower().endswith(IMG_EXT)]
    # dedupe by filename — some datasets ship a duplicate copy (e.g. natural-images)
    for cls, paths in pools.items():
        seen, uniq = set(), []
        for p in paths:
            b = os.path.basename(p)
            if b not in seen:
                seen.add(b)
                uniq.append(p)
        pools[cls] = uniq
    return pools


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-class", type=int, default=250, help="images to sample per class")
    args = ap.parse_args()

    random.seed(SEED)
    pools = collect_pools()
    print(f"available per class: { {c: len(p) for c, p in pools.items()} }")

    for cls, paths in pools.items():
        dst = DATA / cls
        dst.mkdir(parents=True, exist_ok=True)
        for f in dst.iterdir():  # idempotent: wipe prior copies, keep .gitkeep
            if f.name != ".gitkeep":
                f.unlink()
        random.shuffle(paths)
        chosen = paths[: args.per_class]
        for i, src in enumerate(chosen):
            shutil.copy2(src, dst / f"{cls}_{i:04d}{os.path.splitext(src)[1].lower()}")
        flag = "" if len(chosen) >= args.per_class else "  ⚠️ fewer than requested"
        print(f"  {cls:12s} {len(chosen):4d} copied (of {len(paths)} available){flag}")

    print(f"\ndone → {DATA}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
