"""Property Triage — Image Analyser (Service 2).

POST /analyse  { "image_url": "..." }
            ->  { "room_type": "kitchen", "condition_score": 4, "confidence": 0.91 }

Loads the trained EfficientNet-B0 (model.pth + classes.json) and classifies the
room type; below CONFIDENCE_THRESHOLD it returns "uncertain". If no model file is
present yet it runs as a STUB returning the same schema, so n8n and the LangGraph
agent can integrate before training finishes.

Condition score (1-5) is a documented placeholder for now — the second head is
future work (labels bootstrapped with Gemini Vision); the graded metric is
room-type accuracy.

Run locally (from the code/ directory):
    ../.venv/bin/python -m uvicorn image_analyser.main:app --port 8002
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path

import requests
from fastapi import FastAPI
from pydantic import BaseModel

HERE = Path(__file__).parent
MODEL_PATH = HERE / "model.pth"
CLASSES_PATH = HERE / "classes.json"
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.55"))
DOWNLOAD_TIMEOUT = int(os.getenv("DOWNLOAD_TIMEOUT", "20"))
PLACEHOLDER_CONDITION = 3  # until the condition head is trained

app = FastAPI(title="Property Triage — Image Analyser")

_model = None
_classes: list[str] = []
_transform = None


def _load_model():
    """Lazy-load the model on first use. Returns (model, classes) or (None, []) in stub mode."""
    global _model, _classes, _transform
    if _model is not None or not (MODEL_PATH.exists() and CLASSES_PATH.exists()):
        return _model, _classes
    import torch
    from torch import nn
    from torchvision import models, transforms

    _classes = json.loads(CLASSES_PATH.read_text(encoding="utf-8"))
    net = models.efficientnet_b0(weights=None)
    net.classifier[1] = nn.Linear(net.classifier[1].in_features, len(_classes))
    net.load_state_dict(torch.load(MODEL_PATH, map_location="cpu"))
    net.eval()
    _model = net
    _transform = transforms.Compose([
        transforms.Resize(256),
        transforms.CenterCrop(224),
        transforms.ToTensor(),
        transforms.Normalize((0.485, 0.456, 0.406), (0.229, 0.224, 0.225)),
    ])
    return _model, _classes


def pick_label(probs: list[float], classes: list[str], threshold: float) -> tuple[str, float]:
    """Top class + confidence, or 'uncertain' when the best probability is too low."""
    best = max(range(len(probs)), key=lambda i: probs[i])
    conf = float(probs[best])
    return (classes[best] if conf >= threshold else "uncertain"), round(conf, 3)


class AnalyseRequest(BaseModel):
    image_url: str


@app.get("/health")
def health():
    return {"status": "ok", "model_loaded": MODEL_PATH.exists() and CLASSES_PATH.exists()}


@app.post("/analyse")
def analyse(req: AnalyseRequest):
    model, classes = _load_model()
    if model is None:  # stub mode — model not trained yet
        return {"room_type": "uncertain", "condition_score": None,
                "confidence": 0.0, "note": "stub: model not trained yet"}

    import torch
    from PIL import Image

    resp = requests.get(req.image_url, timeout=DOWNLOAD_TIMEOUT)
    resp.raise_for_status()
    image = Image.open(io.BytesIO(resp.content)).convert("RGB")
    with torch.no_grad():
        logits = model(_transform(image).unsqueeze(0))
        probs = torch.softmax(logits, dim=1)[0].tolist()
    room_type, confidence = pick_label(probs, classes, CONFIDENCE_THRESHOLD)
    # condition only applies to a recognised room — null for not_a_room / uncertain
    condition = PLACEHOLDER_CONDITION if room_type not in ("uncertain", "not_a_room") else None
    return {"room_type": room_type, "condition_score": condition, "confidence": confidence}
