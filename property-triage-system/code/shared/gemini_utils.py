"""Shared Gemini client for all services.

The API key comes from AWS Secrets Manager (see aws_utils.get_gemini_api_key),
so services authenticate to Google using only their AWS credentials.
"""
from __future__ import annotations

import os
from functools import lru_cache

from google import genai

from .aws_utils import get_gemini_api_key

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")


@lru_cache(maxsize=1)
def client() -> genai.Client:
    return genai.Client(api_key=get_gemini_api_key())


def generate(prompt: str, *, temperature: float = 0.7, model: str | None = None) -> str:
    """Single-shot text generation; returns the response text."""
    resp = client().models.generate_content(
        model=model or GEMINI_MODEL,
        contents=prompt,
        config={"temperature": temperature},
    )
    return (resp.text or "").strip()
