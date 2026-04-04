"""
Step 0: Count PDF pages and decide execution strategy.
Lightweight Lambda — downloads PDF from S3, counts pages, returns routing info.
"""
import json
import os
import tempfile

import boto3
import PyPDF2

s3 = boto3.client("s3")

PAGE_THRESHOLD = int(os.environ.get("PAGE_THRESHOLD", "50"))


def lambda_handler(event, context):
    bucket = event["bucket"]
    key = event["key"]

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        s3.download_file(bucket, key, tmp.name)
        reader = PyPDF2.PdfReader(tmp.name)
        page_count = len(reader.pages)
        os.unlink(tmp.name)

    strategy = "single" if page_count <= PAGE_THRESHOLD else "stepfunctions"
    print(f"[Router] {key}: {page_count} pages → {strategy} (threshold={PAGE_THRESHOLD})")

    return {
        "bucket": bucket,
        "key": key,
        "pageCount": page_count,
        "strategy": strategy,
    }
