# Model Card — Image Analyser (Service 2)

## Overview
Multi-task model for property photos. Fine-tuned **EfficientNet-B0** (ImageNet
weights, frozen backbone) with **two heads** on the shared backbone — a room-type
head and a condition-score head, per the spec ("add a second output head for a
condition score from 1 to 5"). Serves `POST /analyse` →
`{room_type, condition_score, confidence}`; below a 0.55 confidence threshold the
room is `"uncertain"`.

- **Task:** room type (7-class) + condition score (1–5) — one backbone, two heads
- **Classes:** `bathroom, bedroom, exterior, kitchen, living_room, other, not_a_room`
- **Base model:** `efficientnet_b0` (torchvision, `IMAGENET1K_V1`), backbone frozen
- **Trained:** 14 epochs, Adam lr=1e-3, MPS (Apple Silicon), `train.py` (dual masked
  loss — room CE on all images, condition CE on the Gemini-labelled subset)

> **Serving note (hybrid):** room type is served by this CNN; the **condition score
> is served by Gemini Vision at inference** (see *Condition score* below). The trained
> condition head satisfies the spec and is the offline fallback, but Gemini is reliable
> across condition types the available training data can't cover.

`other` = dining rooms / non-standard rooms. `not_a_room` is a **reject class** we
added (not in the original spec) so the model can flag non-property photos instead
of being forced to pick a room — see *Robustness* below.

## Data
500 images per class (3,500 total), balanced, sourced via `prepare_data.py` (kagglehub):

| Class | Source |
|-------|--------|
| kitchen, bathroom, bedroom, living_room, other(=dining) | `robinreni/house-rooms-image-dataset` |
| exterior | `mikhailma/house-rooms-streets-image-dataset` (street_data) |
| not_a_room | `prasunroy/natural-images` (airplane, car, cat, dog, flower, fruit, motorbike, person) |

15% held-out validation split (seed 42). Dataset licenses: see each Kaggle page.

## Results

**Room-type accuracy on fresh, unseen images (argmax, 40/class): 84.6%** — clears
the >75% bar. (Evaluated on images excluded from `data/`; `not_a_room` excluded so
this reflects room performance only.)

| Class | Accuracy | Main confusions |
|-------|----------|-----------------|
| exterior | 100% | — |
| bathroom | 88% | kitchen |
| bedroom | 85% | living_room |
| kitchen | 85% | living_room, other |
| living_room | 80% | other, bedroom |
| other (dining) | 70% | living_room, kitchen |

7-class validation accuracy (incl. `not_a_room`), dual-head model: **84.4%**.
Validation confusion matrix (rows=true, cols=pred):

```
         bathr bedro exter kitch livin not_a other
bathroom    62    1     0    5     1     0    1
bedroom      1   62     0    2    12     0    3
exterior     0    0    90    0     0     0    0
kitchen      0    1     0   56     3     0    9
living_rm    1    8     0    4    50     0    8
not_a_room   0    0     0    0     0    71    0
other        2    2     1    8     8     0   46
```

Doubling the data from 250→500/class lifted the weak classes notably:
`living_room` 65%→80%, `other` 60%→70%, overall 79.6%→84.6%.

## Robustness (out-of-distribution)
With the `not_a_room` reject class, non-property images are flagged rather than
forced into a room:

| Input | Prediction |
|-------|------------|
| dog / cat photo | `not_a_room` (0.79 / 0.95) |
| scanned document / diagram | `not_a_room` (0.80 / 0.73) |
| random-noise image | `not_a_room` (0.56) |
| solid-colour image | `uncertain` (0.37) |

Before the reject class, a solid-colour image was confidently misclassified as
`kitchen` (1.00); the reject class fixes this.

## Condition score (1–5)
A **real second head** (`cond_head`), trained — not a placeholder. Because the room
datasets carry no condition ground truth, labels were **bootstrapped with Gemini
Vision** (`label_condition.py`, offline) over 846 images: the clean room photos
(mostly 4–5) plus lower-condition sources for range — the *messy-vs-clean-room*
dataset and real apartment/entrance photos (*home-bro-images*). The head trains with
inverse-frequency class weights (the corpus skews to good condition) and reaches
**validation MAE ≈ 0.6** (within ~½ a point of Gemini's label on a 1–5 scale).

**Serving uses Gemini Vision, not the head.** Evaluated on out-of-distribution
"bad room" photos, the trained head was unreliable — it learned *messy/dirty → low*
but mis-scored degraded-but-tidy rooms (e.g. a worn bedroom it rated 5/5 where Gemini
and a human say 1/5). The head can't exceed what the limited training data teaches.
So at inference the condition score comes from **Gemini Vision** (same 1–5 rubric used
for labelling), which is reliable across condition types; the trained head remains as
the spec's "second output head" and as the offline fallback when Gemini is unavailable.
Condition is `null` for `not_a_room` (not a property photo).

## Limitations
- **Dining rooms (`other`) are the weakest class (70%)** — genuinely ambiguous with
  kitchens and living rooms.
- Degenerate inputs (solid colours) can still slip below the reject class into
  `uncertain` rather than `not_a_room`.
- The trained condition head is unreliable on bad-but-tidy rooms (see *Condition
  score*); inference serves the Gemini-Vision score instead.

## Reproduce
```bash
cd code/image_analyser
../../.venv/bin/python prepare_data.py --per-class 500   # rooms; needs ~/.kaggle/kaggle.json
# condition labels (Gemini Vision) — needs AWS creds for the Gemini secret:
AWS_PROFILE=course ../../.venv/bin/python label_condition.py --per-class 70
../../.venv/bin/python train.py --epochs 14              # writes model.pth + classes.json
```
