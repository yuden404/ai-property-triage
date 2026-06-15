"""Reproduce the Image Analyser's held-out validation metrics from model.pth.

Uses the SAME data, transforms, and seed-42 split as train.py, so the reported
numbers are reproducible from the committed model.pth + classes.json — no hidden
"fresh images" set. Writes eval_metrics.json next to the model.

Run (from code/image_analyser/):
    ../../.venv/bin/python eval.py
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import torch
from torch.utils.data import DataLoader, Subset

HERE = Path(__file__).parent
_spec = importlib.util.spec_from_file_location("train", str(HERE / "train.py"))
train = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(train)


def main() -> int:
    classes = json.loads((HERE / "classes.json").read_text())
    labels = json.loads((HERE / "condition_labels.json").read_text()) \
        if (HERE / "condition_labels.json").exists() else {}
    items = train.build_items(classes, labels)

    # identical split to train.py (seed 42, 15% val)
    perm = torch.randperm(len(items), generator=torch.Generator().manual_seed(42)).tolist()
    val_idx = perm[int(len(items) * 0.85):]
    eval_tf = train.transforms.Compose([
        train.transforms.Resize(256), train.transforms.CenterCrop(224),
        train.transforms.ToTensor(),
        train.transforms.Normalize(train.IMAGENET_MEAN, train.IMAGENET_STD),
    ])
    val_dl = DataLoader(Subset(train.RoomCondDataset(items, eval_tf), val_idx),
                        batch_size=32, num_workers=0)

    device = train._device()
    net = train.MultiHeadNet(len(classes), pretrained=False).to(device)
    net.load_state_dict(torch.load(HERE / "model.pth", map_location=device))
    net.eval()

    room_acc, confusion, cond_acc, cond_mae = train.evaluate(net, val_dl, device, len(classes))
    per_class = {}
    for i, c in enumerate(classes):
        tot = int(confusion[i].sum())
        per_class[c] = round(int(confusion[i][i]) / tot, 3) if tot else None

    metrics = {
        "room_val_accuracy": round(room_acc, 4),
        "room_accuracy_pass_75": room_acc > 0.75,
        "condition_val_accuracy": round(cond_acc, 4),
        "condition_val_mae": round(cond_mae, 3),
        "per_class_room_accuracy": per_class,
        "n_val_items": len(val_idx),
        "seed": 42, "val_frac": 0.15, "classes": classes,
    }
    (HERE / "eval_metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(json.dumps(metrics, indent=2))
    print(f"\nroom_val_accuracy={room_acc:.4f} ({'PASS >75%' if room_acc > 0.75 else 'FAIL'})  "
          f"· cond_mae={cond_mae:.2f}  · wrote eval_metrics.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
