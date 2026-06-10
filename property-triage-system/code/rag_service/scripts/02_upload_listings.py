"""Upload the generated listings to S3 (the Knowledge Base data source).

Creates the bucket if needed, then syncs code/rag_service/listings_data/*.txt
to s3://<bucket>/listings/.

Run:  AWS_PROFILE=course .venv/bin/python code/rag_service/scripts/02_upload_listings.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2]))  # → code/ on the path

from shared.aws_utils import REGION, session  # noqa: E402

BUCKET = "property-triage-listings-yrokach"
PREFIX = "listings/"
DATA_DIR = HERE.parents[1] / "listings_data"


def main() -> int:
    s3 = session().client("s3")

    # Create the bucket if it doesn't exist (us-east-1 needs no LocationConstraint).
    try:
        s3.head_bucket(Bucket=BUCKET)
        print(f"bucket exists: s3://{BUCKET}")
    except s3.exceptions.ClientError:
        s3.create_bucket(Bucket=BUCKET)
        print(f"bucket created: s3://{BUCKET} ({REGION})")

    files = sorted(DATA_DIR.glob("*.txt"))
    assert files, f"no listings found in {DATA_DIR} — run 01_generate_listings.py first"
    for f in files:
        s3.upload_file(str(f), BUCKET, PREFIX + f.name)
    print(f"uploaded {len(files)} listings to s3://{BUCKET}/{PREFIX}")

    listed = s3.list_objects_v2(Bucket=BUCKET, Prefix=PREFIX)["KeyCount"]
    print(f"verified in S3: {listed} objects under /{PREFIX}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
