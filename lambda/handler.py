import json
import os
import tempfile
import urllib.parse

import boto3

from pageindex import page_index_main
from pageindex.utils import ConfigLoader

s3 = boto3.client("s3")

MODEL = os.environ.get("PAGEINDEX_MODEL", "bedrock/us.anthropic.claude-sonnet-4-6-20250929-v1:0")
OUTPUT_PREFIX = os.environ.get("OUTPUT_PREFIX", "indexes/")


def lambda_handler(event, context):
    record = event["Records"][0]["s3"]
    bucket = record["bucket"]["name"]
    key = urllib.parse.unquote_plus(record["object"]["key"])

    print(f"Processing s3://{bucket}/{key}")

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        s3.download_file(bucket, key, tmp.name)
        tmp_path = tmp.name

    try:
        opt = ConfigLoader().load(
            {
                "model": MODEL,
                "if_add_node_summary": "yes",
                "if_add_doc_description": "yes",
                "if_add_node_id": "yes",
            }
        )

        result = page_index_main(tmp_path, opt)
    finally:
        os.unlink(tmp_path)

    base_name = os.path.splitext(os.path.basename(key))[0]
    output_key = f"{OUTPUT_PREFIX}{base_name}_structure.json"

    body = json.dumps(result, ensure_ascii=False, indent=2)
    s3.put_object(
        Bucket=bucket,
        Key=output_key,
        Body=body,
        ContentType="application/json",
    )

    print(f"Index saved to s3://{bucket}/{output_key}")

    return {
        "statusCode": 200,
        "body": json.dumps(
            {
                "source": f"s3://{bucket}/{key}",
                "index": f"s3://{bucket}/{output_key}",
            }
        ),
    }
