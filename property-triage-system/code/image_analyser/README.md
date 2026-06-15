# Image Analyser (Service 2)

Classifies a property photo into a room type and a 1–5 condition score.
Model: **EfficientNet-B0** fine-tuned (ImageNet weights, frozen backbone + retrained head).

## 1. Get the data

Source ≥200 labelled images (aim for ~150–250 per class) and drop them into one
folder per class — the folder name IS the label:

```
data/
├── kitchen/
├── bathroom/
├── bedroom/
├── living_room/
├── exterior/      # house/building facade, street view
└── other/         # dining room, hallway, balcony, garden, garage, office, empty room
```

Fast source: [Kaggle](https://www.kaggle.com/) — search "house rooms image dataset"
(kitchen / bathroom / bedroom / living room) and a "house exterior / street view"
dataset for `exterior`. **Cite each dataset's license here** when you add it.

> The `data/` images and `model.pth` are git-ignored (large / licensed) — they're
> reproduced from this README, not committed.

## 2. Train

```bash
# from code/image_analyser/  (after the PyTorch lesson)
../../.venv/bin/python train.py --epochs 12
```

Outputs `model.pth` + `classes.json`, and prints validation accuracy (target
**>75%**) and a confusion matrix. Copy those into `docs/model_card.md`.

## 3. Serve

The FastAPI service (`main.py`, port 8002) loads `model.pth` and exposes
`POST /analyse` → `{room_type, condition_score, confidence}`, returning
`"uncertain"` below a confidence threshold. Until a model is trained it runs as
a stub returning the same schema.

## Condition score (1–5)

Room datasets have no condition ground truth, so condition labels were
**bootstrapped with Gemini Vision** (`label_condition.py`, 846 images: clean
rooms + messy/worn sources for range) and a **second head (`cond_head`) was
trained** on them. On out-of-distribution bad rooms the trained head was
unreliable, so at serving the condition score is provided by **Gemini Vision**
(same 1–5 rubric); the trained head is the spec's "second output head" and the
offline fallback. The graded metric is room-type accuracy. See `docs/model_card.md`.
