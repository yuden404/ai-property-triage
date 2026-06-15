"""Bootstrap condition-score (1-5) labels for the room images with Gemini Vision.

The public room datasets carry NO condition ground truth, so we distil a capable
vision model's judgement into labels, then train our own CNN head on them (see
train.py). Gemini is used ONCE here, offline — inference at serving time is 100%
our PyTorch model, no Gemini call.

Run (from code/image_analyser/, AWS profile must reach the Gemini secret):
    AWS_PROFILE=course ../../.venv/bin/python label_condition.py --per-class 70

Output: condition_labels.json  -> { "bedroom/bedroom_0000.jpg": 4, ... }
Re-runnable: already-labelled images are skipped, so you can resume / top up.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Reuse the project's Gemini key plumbing (Secrets Manager via AWS creds).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # .../code
from shared.aws_utils import get_gemini_api_key  # noqa: E402

from google import genai  # noqa: E402
from google.genai import types  # noqa: E402

HERE = Path(__file__).parent
DATA_DIR = HERE / "data"
# Condition-only sources (no room-type label): messy-vs-clean rooms + real
# apartment/entrance photos (home-bro-images) for varied real-world condition.
COND_ONLY_DIRS = [HERE / "data_messy", HERE / "data_varied"]
LABELS_PATH = HERE / "condition_labels.json"
GEMINI_MODEL = "gemini-2.5-flash"

# not_a_room has no meaningful "condition" — exclude it from labelling/training.
ROOM_CLASSES = ["kitchen", "bathroom", "bedroom", "living_room", "exterior", "other"]
# Keys in condition_labels.json are paths relative to HERE (this dir), so the
# trainer can resolve any source — clean room data/ images AND data_messy/.

PROMPT = (
    "You are a property inspector scoring a real-estate photo. Rate the physical "
    "CONDITION of the space on an integer scale of 1 to 5:\n"
    "1 = very poor: major damage, mould, broken/missing fixtures, derelict.\n"
    "2 = poor: heavily worn or dated, needs significant repair or renovation.\n"
    "3 = average: functional and maintained but dated or with visible wear.\n"
    "4 = good: well-maintained, clean, modern, only minor wear.\n"
    "5 = excellent: renovated or new, pristine, high-end finish.\n"
    "Judge condition only (wear, damage, finish, cleanliness) — NOT size, style, or price. "
    "Reply with ONLY a single digit 1-5, nothing else."
)

_local = threading.local()


def _client() -> genai.Client:
    """One Gemini client per worker thread."""
    c = getattr(_local, "client", None)
    if c is None:
        c = _local.client = genai.Client(api_key=_API_KEY)
    return c


def score_image(path: Path) -> int | None:
    """Ask Gemini for a 1-5 condition score; return the int or None on failure."""
    try:
        data = path.read_bytes()
        mime = {".png": "image/png", ".webp": "image/webp"}.get(path.suffix.lower(), "image/jpeg")
        resp = _client().models.generate_content(
            model=GEMINI_MODEL,
            contents=[types.Part.from_bytes(data=data, mime_type=mime), PROMPT],
            config={"temperature": 0},
        )
        for ch in (resp.text or ""):
            if ch in "12345":
                return int(ch)
    except Exception:
        return None
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-class", type=int, default=70, help="images to label per room class")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    global _API_KEY
    _API_KEY = get_gemini_api_key()

    labels: dict[str, int] = {}
    if LABELS_PATH.exists():
        labels = json.loads(LABELS_PATH.read_text(encoding="utf-8"))
        print(f"resuming — {len(labels)} images already labelled")

    # Build the to-do list (keys are paths relative to HERE):
    #  • first N per clean room class from data/  (mostly good condition → 4-5)
    #  • ALL messy rooms from data_messy/         (low condition → 2-3) for range
    todo: list[Path] = []
    for cls in ROOM_CLASSES:
        d = DATA_DIR / cls
        if not d.is_dir():
            print(f"  (skip {cls}: folder missing)")
            continue
        imgs = sorted(p for p in d.iterdir()
                      if p.suffix.lower() in {".jpg", ".jpeg", ".png"})[: args.per_class]
        todo += imgs
    for cd in COND_ONLY_DIRS:
        if cd.is_dir():
            todo += sorted(p for p in cd.iterdir()
                           if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"})
    todo = [p for p in todo if str(p.relative_to(HERE)) not in labels]
    print(f"labelling {len(todo)} images with {GEMINI_MODEL} ({args.workers} workers)...")

    done = 0
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(score_image, p): p for p in todo}
        for fut in as_completed(futs):
            p = futs[fut]
            score = fut.result()
            if score is not None:
                key = str(p.relative_to(HERE))
                with lock:
                    labels[key] = score
                    done += 1
                    if done % 25 == 0:
                        LABELS_PATH.write_text(json.dumps(labels, indent=2), encoding="utf-8")
                        print(f"  {done}/{len(todo)} labelled (checkpoint saved)")

    LABELS_PATH.write_text(json.dumps(labels, indent=2), encoding="utf-8")
    # Distribution sanity print — flag if Gemini collapsed to one value.
    dist = {s: sum(1 for v in labels.values() if v == s) for s in range(1, 6)}
    print(f"\ndone — {len(labels)} labels total. distribution 1..5: {dist}")
    print(f"saved {LABELS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
