# Model Card — Image Analyser (Service 2)

## Overview
Room-type classifier for property photos. Fine-tuned **EfficientNet-B0** (ImageNet
weights, frozen backbone, retrained classifier head). Serves `POST /analyse` →
`{room_type, condition_score, confidence}`; below a 0.55 confidence threshold it
returns `"uncertain"`.

- **Task:** 7-class single-label image classification
- **Classes:** `bathroom, bedroom, exterior, kitchen, living_room, other, not_a_room`
- **Base model:** `efficientnet_b0` (torchvision, `IMAGENET1K_V1`), backbone frozen
- **Trained:** 12 epochs, Adam lr=1e-3, MPS (Apple Silicon), `train.py`

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

7-class validation accuracy (incl. `not_a_room`): **84.4%**. Validation confusion
matrix (rows=true, cols=pred):

```
         bathr bedro exter kitch livin not_a other
bathroom    57    1     0    4     1     0    1
bedroom      1   56     0    1    15     0    1
exterior     0    0    88    0     0     0    0
kitchen      5    1     0   49     3     0    7
living_rm    1    4     0    4    54     1    5
not_a_room   0    0     0    0     0    85    0
other        2    5     0    7    11     1   54
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
**Not yet a trained output** — returned as a documented placeholder (`3` for rooms,
`null` for `not_a_room`/`uncertain`). Room datasets carry no condition ground truth;
the plan is a second head trained on labels bootstrapped with Gemini Vision. The
graded metric is room-type accuracy.

## Limitations
- **Dining rooms (`other`) are the weakest class (70%)** — genuinely ambiguous with
  kitchens and living rooms.
- Degenerate inputs (solid colours) can still slip below the reject class into
  `uncertain` rather than `not_a_room`.
- Condition score is a placeholder (see above).

## Reproduce
```bash
cd code/image_analyser
../../.venv/bin/python prepare_data.py --per-class 500   # needs ~/.kaggle/kaggle.json
../../.venv/bin/python train.py --epochs 12              # writes model.pth + classes.json
```
