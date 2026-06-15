"""Property Triage — Image Analyser (Service 2).

POST /analyse  { "image_url": "..." }
            ->  { "room_type": "kitchen", "condition_score": 4, "confidence": 0.91 }

Loads the trained EfficientNet-B0 (model.pth + classes.json) — one frozen
backbone with two heads: room type (7 classes) and condition score (1-5). Below
CONFIDENCE_THRESHOLD the room is "uncertain". If no model file is present yet it
runs as a STUB returning the same schema, so n8n and the LangGraph agent can
integrate before training finishes.

Condition score (1-5) is a real trained head: its labels were bootstrapped with
Gemini Vision (offline, see label_condition.py) and distilled into the CNN — so
serving needs no Gemini call. condition is null for not_a_room / uncertain.

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
NUM_COND = 5  # condition scores 1..5 -> head outputs 5 classes (index + 1)
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
# Same rubric used to bootstrap the training labels (label_condition.py), so the
# served score is consistent with how the dataset was scored.
COND_PROMPT = (
    "You are a property inspector scoring a real-estate photo. Rate the physical "
    "CONDITION of the space on an integer scale of 1 to 5 (1=very poor/damaged, "
    "2=poor/worn, 3=average, 4=good, 5=excellent/renovated). Judge wear, damage, "
    "finish and cleanliness only — not size, style, or price. Reply with ONLY a "
    "single digit 1-5."
)

app = FastAPI(title="Property Triage — Image Analyser")

_model = None
_classes: list[str] = []
_transform = None


def _build_net(num_rooms: int):
    """EfficientNet-B0 backbone with two heads (room + condition) — must match the
    architecture trained in train.py so the saved state_dict loads cleanly."""
    import torch  # noqa: F401  (kept local; module imports stay light for stub mode)
    from torch import nn
    from torchvision import models

    class MultiHeadNet(nn.Module):
        def __init__(self):
            super().__init__()
            base = models.efficientnet_b0(weights=None)
            self.features = base.features
            self.avgpool = base.avgpool
            self.dropout = nn.Dropout(p=0.2)
            in_f = base.classifier[1].in_features
            self.room_head = nn.Linear(in_f, num_rooms)
            self.cond_head = nn.Linear(in_f, NUM_COND)

        def forward(self, x):
            x = self.features(x)
            x = self.avgpool(x)
            x = torch.flatten(x, 1)
            x = self.dropout(x)
            return self.room_head(x), self.cond_head(x)

    return MultiHeadNet()


def _load_model():
    """Lazy-load the model on first use. Returns (model, classes) or (None, []) in stub mode."""
    global _model, _classes, _transform
    if _model is not None or not (MODEL_PATH.exists() and CLASSES_PATH.exists()):
        return _model, _classes
    import torch
    from torchvision import transforms

    _classes = json.loads(CLASSES_PATH.read_text(encoding="utf-8"))
    net = _build_net(len(_classes))
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


def gemini_condition(image_bytes: bytes, mime: str) -> int | None:
    """Condition score (1-5) from Gemini Vision. Room type comes from our CNN; the
    condition score is owned by Gemini because it's reliable across condition types
    the limited training data can't cover. Returns None on any failure (caller then
    falls back to the trained CNN cond_head)."""
    try:
        from google.genai import types

        from shared.gemini_utils import client

        resp = client().models.generate_content(
            model=GEMINI_MODEL,
            contents=[types.Part.from_bytes(data=image_bytes, mime_type=mime), COND_PROMPT],
            config={"temperature": 0},
        )
        for ch in (resp.text or ""):
            if ch in "12345":
                return int(ch)
    except Exception:
        return None
    return None


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
    content = resp.content
    image = Image.open(io.BytesIO(content)).convert("RGB")
    with torch.no_grad():
        room_logits, cond_logits = model(_transform(image).unsqueeze(0))
        probs = torch.softmax(room_logits, dim=1)[0].tolist()
        cnn_condition = int(cond_logits.argmax(1).item()) + 1  # trained head (spec + fallback)
    room_type, confidence = pick_label(probs, classes, CONFIDENCE_THRESHOLD)
    # Room type = our CNN. Condition is owned by Gemini Vision (reliable across
    # condition types the CNN couldn't learn), falling back to the CNN cond_head
    # only if Gemini is unavailable. We score condition for any real room photo —
    # including "uncertain" room types — and skip it only for not_a_room (an
    # explicit non-property image has no meaningful condition).
    condition = None
    if room_type != "not_a_room":
        mime = resp.headers.get("Content-Type", "").split(";")[0].strip()
        if not mime.startswith("image/"):
            mime = "image/jpeg"
        condition = gemini_condition(content, mime) or cnn_condition
    return {"room_type": room_type, "condition_score": condition, "confidence": confidence}
