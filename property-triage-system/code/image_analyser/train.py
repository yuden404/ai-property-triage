"""Train the property-image model: room-type head + condition-score head.

Per the project spec: fine-tune a pretrained CNN to classify room types AND
"add a second output head for a condition score from 1 (poor) to 5 (excellent)".

One EfficientNet-B0 backbone (frozen, ImageNet) feeds two linear heads:
  • room_head  — 7 room types (kitchen/bathroom/.../not_a_room), labels = folder name.
  • cond_head  — 5 condition classes (score 1-5), labels bootstrapped with Gemini
                 Vision into condition_labels.json (see label_condition.py).

Condition labels exist only for a subset of images, and the messy-room images
(data_messy/, the low-condition end) have no room-type label — so each loss is
masked (ignore_index=-1) to the samples that carry that label. Condition uses
inverse-frequency class weights because the corpus skews toward good condition.

Data layout:
    data/<room>/*.jpg                 room-type labels (all images)
    data_messy/*.{jpg,png}            condition-only (low end), no room label
    condition_labels.json            { "data/kitchen/x.jpg": 4, "data_messy/m.png": 2, ... }

Usage (from code/image_analyser/):
    ../../.venv/bin/python train.py --epochs 12
Outputs: model.pth (both heads) + classes.json (room label order); prints room
accuracy + confusion matrix and condition accuracy + MAE.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

# macOS python.org builds often lack system CA certs, so torchvision's
# pretrained-weights download fails with CERTIFICATE_VERIFY_FAILED. Point SSL at
# certifi's CA bundle so the download verifies (valid CAs — not a bypass).
try:
    import certifi

    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
except ImportError:
    pass

import torch
from PIL import Image
from torch import nn
from torch.utils.data import DataLoader, Dataset, Subset
from torchvision import models, transforms

HERE = Path(__file__).parent
DATA_DIR = HERE / "data"
# Condition-only image sources (no room-type label): messy-vs-clean rooms +
# real apartment/entrance photos (home-bro-images) for varied real-world condition.
COND_ONLY_DIRS = [HERE / "data_messy", HERE / "data_varied"]
LABELS_PATH = HERE / "condition_labels.json"
NUM_COND = 5  # condition scores 1..5 -> class indices 0..4
IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)
IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def _device() -> torch.device:
    if torch.backends.mps.is_available():
        return torch.device("mps")
    if torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


class MultiHeadNet(nn.Module):
    """EfficientNet-B0 (frozen backbone) with two linear heads: room + condition."""

    def __init__(self, num_rooms: int, num_cond: int = NUM_COND, pretrained: bool = True):
        super().__init__()
        weights = models.EfficientNet_B0_Weights.IMAGENET1K_V1 if pretrained else None
        base = models.efficientnet_b0(weights=weights)
        self.features = base.features
        self.avgpool = base.avgpool
        self.dropout = nn.Dropout(p=0.2)  # mirrors efficientnet's classifier[0]
        in_f = base.classifier[1].in_features  # 1280
        self.room_head = nn.Linear(in_f, num_rooms)
        self.cond_head = nn.Linear(in_f, num_cond)
        for p in self.features.parameters():
            p.requires_grad = False

    def forward(self, x):
        x = self.features(x)
        x = self.avgpool(x)
        x = torch.flatten(x, 1)
        x = self.dropout(x)
        return self.room_head(x), self.cond_head(x)


class RoomCondDataset(Dataset):
    """Items are (path, room_idx, cond_idx); either label may be -1 (ignored in loss)."""

    def __init__(self, items, transform):
        self.items = items
        self.tf = transform

    def __len__(self):
        return len(self.items)

    def __getitem__(self, i):
        path, room, cond = self.items[i]
        img = Image.open(path).convert("RGB")
        return self.tf(img), room, cond


def build_items(classes: list[str], labels: dict[str, int]):
    """One entry per image. Room images carry a room label + (if Gemini-labelled)
    a condition label; messy images carry only a condition label."""
    items = []
    for ci, cls in enumerate(classes):
        for p in sorted((DATA_DIR / cls).iterdir()):
            if p.suffix.lower() not in IMG_EXTS:
                continue
            cond = labels.get(f"data/{cls}/{p.name}")
            items.append((p, ci, (cond - 1) if cond else -1))
    for cd in COND_ONLY_DIRS:
        if not cd.is_dir():
            continue
        for p in sorted(cd.iterdir()):
            if p.suffix.lower() not in IMG_EXTS:
                continue
            cond = labels.get(f"{cd.name}/{p.name}")
            if cond:  # condition-only images are useful only once labelled
                items.append((p, -1, cond - 1))
    return items


def cond_class_weights(items, indices) -> torch.Tensor:
    """Inverse-frequency weights over the 5 condition classes (train split only),
    so the model isn't swamped by the abundant good-condition (4-5) images."""
    counts = torch.zeros(NUM_COND)
    for i in indices:
        c = items[i][2]
        if c != -1:
            counts[c] += 1
    counts = counts.clamp(min=1.0)
    w = counts.sum() / (NUM_COND * counts)
    return w / w.mean()


@torch.no_grad()
def evaluate(model, loader, device, num_rooms):
    model.eval()
    room_correct = room_total = 0
    confusion = torch.zeros(num_rooms, num_rooms, dtype=torch.int64)
    cond_correct = cond_total = 0
    cond_abs_err = 0.0
    for x, room, cond in loader:
        rl, cl = model(x.to(device))
        rp, cp = rl.argmax(1).cpu(), cl.argmax(1).cpu()
        rm = room != -1
        for t, p in zip(room[rm], rp[rm]):
            confusion[t, p] += 1
        room_correct += (rp[rm] == room[rm]).sum().item()
        room_total += int(rm.sum())
        cm = cond != -1
        cond_correct += (cp[cm] == cond[cm]).sum().item()
        cond_abs_err += (cp[cm] - cond[cm]).abs().sum().item()
        cond_total += int(cm.sum())
    room_acc = room_correct / room_total if room_total else 0.0
    cond_acc = cond_correct / cond_total if cond_total else 0.0
    cond_mae = cond_abs_err / cond_total if cond_total else 0.0
    return room_acc, confusion, cond_acc, cond_mae


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=12)
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--val-frac", type=float, default=0.15)
    ap.add_argument("--cond-loss-weight", type=float, default=1.5)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    if not DATA_DIR.is_dir():
        raise SystemExit(f"{DATA_DIR} missing — run prepare_data.py first.")
    classes = sorted(d.name for d in DATA_DIR.iterdir() if d.is_dir())
    labels = json.loads(LABELS_PATH.read_text(encoding="utf-8")) if LABELS_PATH.exists() else {}
    if not labels:
        raise SystemExit(f"{LABELS_PATH} missing/empty — run label_condition.py first.")

    items = build_items(classes, labels)
    n_cond = sum(1 for it in items if it[2] != -1)
    n_room = sum(1 for it in items if it[1] != -1)
    print(f"items={len(items)} · room-labelled={n_room} · cond-labelled={n_cond} · rooms={classes}")

    train_tf = transforms.Compose([
        transforms.RandomResizedCrop(224, scale=(0.7, 1.0)),
        transforms.RandomHorizontalFlip(),
        transforms.ColorJitter(0.2, 0.2, 0.2),
        transforms.ToTensor(),
        transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD),
    ])
    eval_tf = transforms.Compose([
        transforms.Resize(256),
        transforms.CenterCrop(224),
        transforms.ToTensor(),
        transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD),
    ])
    perm = torch.randperm(len(items), generator=torch.Generator().manual_seed(args.seed)).tolist()
    cut = int(len(items) * (1 - args.val_frac))
    train_idx, val_idx = perm[:cut], perm[cut:]
    train_dl = DataLoader(Subset(RoomCondDataset(items, train_tf), train_idx),
                          batch_size=args.batch_size, shuffle=True, num_workers=2)
    val_dl = DataLoader(Subset(RoomCondDataset(items, eval_tf), val_idx),
                        batch_size=args.batch_size, shuffle=False, num_workers=2)

    device = _device()
    model = MultiHeadNet(len(classes)).to(device)
    room_crit = nn.CrossEntropyLoss(ignore_index=-1)
    cond_crit = nn.CrossEntropyLoss(ignore_index=-1,
                                    weight=cond_class_weights(items, train_idx).to(device))
    optimizer = torch.optim.Adam((p for p in model.parameters() if p.requires_grad), lr=args.lr)
    print(f"device={device} · train batches={len(train_dl)} · val batches={len(val_dl)} · "
          f"cond_weights={cond_class_weights(items, train_idx).tolist()}")

    best_score, best_state = -1.0, None
    for epoch in range(1, args.epochs + 1):
        model.train()
        running = 0.0
        for x, room, cond in train_dl:
            x, room, cond = x.to(device), room.to(device), cond.to(device)
            optimizer.zero_grad()
            rl, cl = model(x)
            loss = x.new_zeros(())
            if (room != -1).any():
                loss = loss + room_crit(rl, room)
            if (cond != -1).any():
                loss = loss + args.cond_loss_weight * cond_crit(cl, cond)
            loss.backward()
            optimizer.step()
            running += float(loss) * x.size(0)
        room_acc, _, cond_acc, cond_mae = evaluate(model, val_dl, device, len(classes))
        print(f"epoch {epoch:2d}/{args.epochs} · loss={running / len(train_idx):.3f} · "
              f"room_acc={room_acc:.3f} · cond_acc={cond_acc:.3f} · cond_mae={cond_mae:.2f}")
        # Select on room accuracy (the graded metric) minus a small MAE penalty.
        score = room_acc - 0.05 * cond_mae
        if score >= best_score:
            best_score, best_state = score, {k: v.cpu() for k, v in model.state_dict().items()}

    if best_state:
        model.load_state_dict(best_state)
    room_acc, confusion, cond_acc, cond_mae = evaluate(model, val_dl, device, len(classes))
    print(f"\nBest checkpoint — room_acc={room_acc:.3f} "
          f"({'PASS >75%' if room_acc > 0.75 else 'below 75%'}) · "
          f"cond_acc={cond_acc:.3f} · cond_mae={cond_mae:.2f}")
    print("Room confusion (rows=true, cols=pred):")
    print("        " + " ".join(f"{c[:5]:>6}" for c in classes))
    for i, c in enumerate(classes):
        print(f"{c[:7]:>7} " + " ".join(f"{int(n):6d}" for n in confusion[i]))

    torch.save(model.state_dict(), HERE / "model.pth")
    (HERE / "classes.json").write_text(json.dumps(classes, indent=2), encoding="utf-8")
    print(f"\nsaved {HERE / 'model.pth'} and classes.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
