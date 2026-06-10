"""Shared AWS helpers for all services.

Credentials resolution: boto3's default chain — locally we use the named
profile (`AWS_PROFILE=course`); on EC2 it's the instance role. Nothing is
hard-coded and no secret ever lives in the repo.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

import boto3

REGION = os.getenv("AWS_REGION", "us-east-1")
GEMINI_SECRET_NAME = os.getenv("GEMINI_SECRET_NAME", "property-triage/gemini-api-key")


def session() -> boto3.session.Session:
    return boto3.session.Session(region_name=REGION)


@lru_cache(maxsize=1)
def get_gemini_api_key() -> str:
    """Fetch the Gemini API key from AWS Secrets Manager (cached per process)."""
    client = session().client("secretsmanager")
    value = client.get_secret_value(SecretId=GEMINI_SECRET_NAME)["SecretString"]
    # Support both a raw string secret and a {"api_key": "..."} JSON secret.
    try:
        return json.loads(value)["api_key"]
    except (json.JSONDecodeError, TypeError, KeyError):
        return value
