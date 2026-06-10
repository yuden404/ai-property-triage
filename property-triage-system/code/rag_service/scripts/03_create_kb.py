"""Create the Bedrock Knowledge Base on S3 Vectors, end to end (idempotent).

Steps:
  1. S3 Vectors: vector bucket + index (1024-dim cosine, for Titan Embed v2)
  2. IAM service role the KB assumes (Titan invoke + data-bucket read + s3vectors)
  3. Knowledge Base (S3_VECTORS storage)
  4. Data source over s3://property-triage-listings-yrokach/listings/
     with chunkingStrategy=NONE  → one listing file = one retrievable chunk
  5. Ingestion job (waits for completion)
  6. Smoke-test retrieval

Run:  AWS_PROFILE=course .venv/bin/python code/rag_service/scripts/03_create_kb.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2]))

from shared.aws_utils import REGION, session  # noqa: E402

ACCOUNT = "928974129332"
DATA_BUCKET = "property-triage-listings-yrokach"
DATA_PREFIX = "listings/"
VEC_BUCKET = "property-triage-vectors-yrokach"
VEC_INDEX = "listings-index"
ROLE_NAME = "PropertyTriageKBRole"
KB_NAME = "property-listings-kb"
DS_NAME = "listings-s3"
EMBED_MODEL_ARN = f"arn:aws:bedrock:{REGION}::foundation-model/amazon.titan-embed-text-v2:0"

s = session()
s3v = s.client("s3vectors")
iam = s.client("iam")
agent = s.client("bedrock-agent")
runtime = s.client("bedrock-agent-runtime")


def step_vector_store() -> str:
    try:
        s3v.get_vector_bucket(vectorBucketName=VEC_BUCKET)
        print(f"[1] vector bucket exists: {VEC_BUCKET}")
    except s3v.exceptions.NotFoundException:
        s3v.create_vector_bucket(vectorBucketName=VEC_BUCKET)
        print(f"[1] vector bucket created: {VEC_BUCKET}")
    try:
        idx = s3v.get_index(vectorBucketName=VEC_BUCKET, indexName=VEC_INDEX)
        print(f"[1] index exists: {VEC_INDEX}")
    except s3v.exceptions.NotFoundException:
        s3v.create_index(
            vectorBucketName=VEC_BUCKET,
            indexName=VEC_INDEX,
            dataType="float32",
            dimension=1024,  # Titan Embed v2
            distanceMetric="cosine",
            metadataConfiguration={"nonFilterableMetadataKeys": ["AMAZON_BEDROCK_TEXT"]},
        )
        idx = s3v.get_index(vectorBucketName=VEC_BUCKET, indexName=VEC_INDEX)
        print(f"[1] index created: {VEC_INDEX}")
    return idx["index"]["indexArn"]


def step_role(index_arn: str) -> str:
    trust = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "bedrock.amazonaws.com"},
            "Action": "sts:AssumeRole",
            "Condition": {"StringEquals": {"aws:SourceAccount": ACCOUNT}},
        }],
    }
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Action": ["bedrock:InvokeModel"], "Resource": [EMBED_MODEL_ARN]},
            {"Effect": "Allow", "Action": ["s3:GetObject", "s3:ListBucket"],
             "Resource": [f"arn:aws:s3:::{DATA_BUCKET}", f"arn:aws:s3:::{DATA_BUCKET}/*"]},
            {"Effect": "Allow", "Action": ["s3vectors:*"],
             "Resource": [f"arn:aws:s3vectors:{REGION}:{ACCOUNT}:bucket/{VEC_BUCKET}",
                          f"arn:aws:s3vectors:{REGION}:{ACCOUNT}:bucket/{VEC_BUCKET}/index/*"]},
        ],
    }
    try:
        arn = iam.get_role(RoleName=ROLE_NAME)["Role"]["Arn"]
        print(f"[2] role exists: {ROLE_NAME}")
    except iam.exceptions.NoSuchEntityException:
        arn = iam.create_role(
            RoleName=ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description="Service role assumed by the Bedrock KB for property listings",
        )["Role"]["Arn"]
        print(f"[2] role created: {ROLE_NAME}")
        time.sleep(10)  # IAM eventual consistency
    iam.put_role_policy(RoleName=ROLE_NAME, PolicyName="kb-access", PolicyDocument=json.dumps(policy))
    return arn


def step_kb(role_arn: str, index_arn: str) -> str:
    for kb in agent.list_knowledge_bases().get("knowledgeBaseSummaries", []):
        if kb["name"] == KB_NAME:
            print(f"[3] KB exists: {kb['knowledgeBaseId']}")
            return kb["knowledgeBaseId"]
    last_err = None
    for attempt in range(6):  # retry: role propagation
        try:
            kb = agent.create_knowledge_base(
                name=KB_NAME,
                description="Synthetic property listings for the triage system",
                roleArn=role_arn,
                knowledgeBaseConfiguration={
                    "type": "VECTOR",
                    "vectorKnowledgeBaseConfiguration": {"embeddingModelArn": EMBED_MODEL_ARN},
                },
                storageConfiguration={
                    "type": "S3_VECTORS",
                    "s3VectorsConfiguration": {"indexArn": index_arn},
                },
            )["knowledgeBase"]
            print(f"[3] KB created: {kb['knowledgeBaseId']}")
            return kb["knowledgeBaseId"]
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(f"    create_knowledge_base attempt {attempt+1}: {str(e)[:140]} — retrying in 10s")
            time.sleep(10)
    raise SystemExit(f"KB creation failed: {last_err}")


def step_datasource(kb_id: str) -> str:
    for ds in agent.list_data_sources(knowledgeBaseId=kb_id).get("dataSourceSummaries", []):
        if ds["name"] == DS_NAME:
            print(f"[4] data source exists: {ds['dataSourceId']}")
            return ds["dataSourceId"]
    ds = agent.create_data_source(
        knowledgeBaseId=kb_id,
        name=DS_NAME,
        dataSourceConfiguration={
            "type": "S3",
            "s3Configuration": {
                "bucketArn": f"arn:aws:s3:::{DATA_BUCKET}",
                "inclusionPrefixes": [DATA_PREFIX],
            },
        },
        vectorIngestionConfiguration={
            "chunkingConfiguration": {"chunkingStrategy": "NONE"}  # 1 file = 1 chunk
        },
    )["dataSource"]
    print(f"[4] data source created: {ds['dataSourceId']}")
    return ds["dataSourceId"]


def step_ingest(kb_id: str, ds_id: str) -> None:
    job = agent.start_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds_id)["ingestionJob"]
    print(f"[5] ingestion started: {job['ingestionJobId']}", end="", flush=True)
    while job["status"] not in ("COMPLETE", "FAILED"):
        time.sleep(5)
        print(".", end="", flush=True)
        job = agent.get_ingestion_job(
            knowledgeBaseId=kb_id, dataSourceId=ds_id, ingestionJobId=job["ingestionJobId"]
        )["ingestionJob"]
    stats = job.get("statistics", {})
    print(f"\n[5] ingestion {job['status']}: indexed={stats.get('numberOfDocumentsScanned')} "
          f"new={stats.get('numberOfNewDocumentsIndexed')} failed={stats.get('numberOfDocumentsFailed')}")
    if job["status"] == "FAILED":
        raise SystemExit(f"ingestion failed: {job.get('failureReasons')}")


def step_test(kb_id: str) -> None:
    q = "3 bedroom apartment in Tel Aviv that needs renovation"
    r = runtime.retrieve(
        knowledgeBaseId=kb_id,
        retrievalQuery={"text": q},
        retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": 3}},
    )
    print(f"[6] retrieval test: '{q}'")
    for i, hit in enumerate(r["retrievalResults"], 1):
        first = hit["content"]["text"].splitlines()[0]
        print(f"    {i}. score={hit['score']:.3f}  {first}")


def main() -> int:
    index_arn = step_vector_store()
    role_arn = step_role(index_arn)
    kb_id = step_kb(role_arn, index_arn)
    ds_id = step_datasource(kb_id)
    step_ingest(kb_id, ds_id)
    step_test(kb_id)
    print(f"\nDONE. KB_ID={kb_id}  (set this in the RAG service .env)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
