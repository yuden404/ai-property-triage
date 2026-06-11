"""Train the property-image room-type classifier (EfficientNet-B0 fine-tune).

Per the project spec: fine-tune a pretrained CNN to classify 6 room types,
write the training loop ourselves, and report test accuracy (target >75%) plus
a confusion matrix for docs/model_card.md.

Data layout (torchvision ImageFolder — the sub-folder name IS the label):
    data/kitchen/   data/bathroom/   data/bedroom/
    data/living_room/   data/exterior/   data/other/

Usage (from code/image_analyser/):
    ../../.venv/bin/python train.py --epochs 12
Outputs: model.pth (weights) + classes.json (label order), and prints the
validation accuracy and confusion matrix.
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
from torch import nn
from torch.utils.data import DataLoader, Subset
from torchvision import datasets, models, transforms

HERE = Path(__file__).parent
DATA_DIR = HERE / "data"
IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)


def _device() -> torch.device:
    if torch.backends.mps.is_available():
        return torch.device("mps")
    if torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


def build_loaders(data_dir: Path, batch_size: int, val_frac: float, seed: int):
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
    # Two views of the same folder so train/val get different transforms, split
    # on a fixed shuffled index so the val set is held out deterministically.
    try:
        train_full = datasets.ImageFolder(str(data_dir), transform=train_tf)
        val_full = datasets.ImageFolder(str(data_dir), transform=eval_tf)
    except FileNotFoundError as exc:
        raise SystemExit(
            f"{exc}\n\nEvery class folder under {data_dir} needs images — "
            "add them per the README, then re-run."
        )
    n = len(train_full)
    perm = torch.randperm(n, generator=torch.Generator().manual_seed(seed)).tolist()
    cut = int(n * (1 - val_frac))
    train_set = Subset(train_full, perm[:cut])
    val_set = Subset(val_full, perm[cut:])
    train_dl = DataLoader(train_set, batch_size=batch_size, shuffle=True, num_workers=2)
    val_dl = DataLoader(val_set, batch_size=batch_size, shuffle=False, num_workers=2)
    return train_dl, val_dl, train_full.classes


def build_model(num_classes: int) -> nn.Module:
    """EfficientNet-B0 pretrained on ImageNet; freeze the backbone, retrain the head."""
    model = models.efficientnet_b0(weights=models.EfficientNet_B0_Weights.IMAGENET1K_V1)
    for p in model.features.parameters():
        p.requires_grad = False
    in_features = model.classifier[1].in_features
    model.classifier[1] = nn.Linear(in_features, num_classes)
    return model


@torch.no_grad()
def evaluate(model, loader, device, num_classes):
    model.eval()
    correct = total = 0
    confusion = torch.zeros(num_classes, num_classes, dtype=torch.int64)
    for x, y in loader:
        preds = model(x.to(device)).argmax(1).cpu()
        for t, p in zip(y, preds):
            confusion[t, p] += 1
        correct += (preds == y).sum().item()
        total += y.numel()
    return (correct / total if total else 0.0), confusion


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=DATA_DIR)
    ap.add_argument("--epochs", type=int, default=12)
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--val-frac", type=float, default=0.15)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    device = _device()
    train_dl, val_dl, classes = build_loaders(args.data, args.batch_size, args.val_frac, args.seed)
    print(f"device={device} · classes={classes} · train batches={len(train_dl)} · val batches={len(val_dl)}")

    model = build_model(len(classes)).to(device)
    criterion = nn.CrossEntropyLoss()
    # Only the new head has requires_grad=True, so optimise just those params.
    optimizer = torch.optim.Adam((p for p in model.parameters() if p.requires_grad), lr=args.lr)

    best_acc, best_state = 0.0, None
    for epoch in range(1, args.epochs + 1):
        model.train()
        running = 0.0
        for x, y in train_dl:
            x, y = x.to(device), y.to(device)
            optimizer.zero_grad()
            loss = criterion(model(x), y)
            loss.backward()
            optimizer.step()
            running += loss.item() * x.size(0)
        acc, _ = evaluate(model, val_dl, device, len(classes))
        print(f"epoch {epoch:2d}/{args.epochs} · train_loss={running / len(train_dl.dataset):.3f} · val_acc={acc:.3f}")
        if acc >= best_acc:
            best_acc, best_state = acc, {k: v.cpu() for k, v in model.state_dict().items()}

    # Restore + report the best checkpoint.
    if best_state:
        model.load_state_dict(best_state)
    acc, confusion = evaluate(model, val_dl, device, len(classes))
    print(f"\nBest val accuracy: {best_acc:.3f}  ({'PASS >75%' if best_acc > 0.75 else 'below 75% — add data / epochs'})")
    print("Confusion matrix (rows=true, cols=pred):")
    print("        " + " ".join(f"{c[:5]:>6}" for c in classes))
    for i, c in enumerate(classes):
        print(f"{c[:7]:>7} " + " ".join(f"{int(n):6d}" for n in confusion[i]))

    torch.save(model.state_dict(), HERE / "model.pth")
    (HERE / "classes.json").write_text(json.dumps(classes, indent=2), encoding="utf-8")
    print(f"\nsaved {HERE / 'model.pth'} and classes.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
