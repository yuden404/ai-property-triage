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
from botocore.config import Config

REGION = os.getenv("AWS_REGION", "us-east-1")
GEMINI_SECRET_NAME = os.getenv("GEMINI_SECRET_NAME", "property-triage/gemini-api-key")

# Bounded timeouts so one stuck AWS call can't park a request worker forever.
_BOTO_CONFIG = Config(connect_timeout=5, read_timeout=30, retries={"max_attempts": 2})


def session() -> boto3.session.Session:
    return boto3.session.Session(region_name=REGION)


def client(service: str):
    """boto3 client with sane timeouts/retries — use this everywhere."""
    return session().client(service, config=_BOTO_CONFIG)


@lru_cache(maxsize=1)
def get_gemini_api_key() -> str:
    """Fetch the Gemini API key from AWS Secrets Manager (cached per process)."""
    value = client("secretsmanager").get_secret_value(SecretId=GEMINI_SECRET_NAME)["SecretString"]
    # Support both a raw string secret and a {"api_key": "..."} JSON secret.
    try:
        return json.loads(value)["api_key"]
    except (json.JSONDecodeError, TypeError, KeyError):
        return value
