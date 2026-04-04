import json
import os
import base64
import tempfile
import urllib.parse

# Patch writable paths for Lambda (must be before importing pageindex)
os.makedirs("/tmp/logs", exist_ok=True)
os.makedirs("/tmp/results", exist_ok=True)
os.chdir("/tmp")

import boto3

from pageindex import page_index_main
from pageindex.utils import ConfigLoader

s3 = boto3.client("s3")

MODEL = os.environ.get("PAGEINDEX_MODEL", "bedrock/us.anthropic.claude-sonnet-4-6-20250929-v1:0")
OUTPUT_PREFIX = os.environ.get("OUTPUT_PREFIX", "indexes/")
BUCKET = os.environ.get("BUCKET_NAME", "")


def _process_pdf(bucket, key):
    """Download PDF from S3, run PageIndex, upload result."""
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
    return {"source": f"s3://{bucket}/{key}", "index": f"s3://{bucket}/{output_key}"}


def lambda_handler(event, context):
    # Mode 0: Called from Step Functions Choice (single Lambda path)
    if event.get("_source") == "stepfunctions-single":
        bucket = event["bucket"]
        key = event["key"]
        result = _process_pdf(bucket, key)
        return {"statusCode": 200, "body": json.dumps(result)}

    # Mode 1: S3 event notification
    if "Records" in event:
        record = event["Records"][0]["s3"]
        bucket = record["bucket"]["name"]
        key = urllib.parse.unquote_plus(record["object"]["key"])
        result = _process_pdf(bucket, key)
        return {"statusCode": 200, "body": json.dumps(result)}

    # Mode 2: Start Step Functions workflow (check before pdf_base64)
    if "start_workflow" in event:
        sfn_client = boto3.client("stepfunctions")
        sfn_arn = event.get("workflow_arn", os.environ.get("WORKFLOW_ARN", ""))
        bucket = event.get("bucket", BUCKET)
        key = event["start_workflow"]

        if "pdf_base64" in event:
            pdf_data = base64.b64decode(event["pdf_base64"])
            s3.put_object(Bucket=bucket, Key=key, Body=pdf_data, ContentType="application/pdf")
            print(f"Uploaded {len(pdf_data)} bytes to s3://{bucket}/{key}")

        resp = sfn_client.start_execution(
            stateMachineArn=sfn_arn,
            input=json.dumps({"bucket": bucket, "key": key}),
        )
        return {
            "statusCode": 200,
            "body": json.dumps({"executionArn": resp["executionArn"]}),
        }

    # Mode 3: Check Step Functions execution status
    if "check_execution" in event:
        sfn_client = boto3.client("stepfunctions")
        resp = sfn_client.describe_execution(executionArn=event["check_execution"])
        result = {
            "status": resp["status"],
            "startDate": resp["startDate"].isoformat(),
        }
        if resp["status"] != "RUNNING":
            result["stopDate"] = resp.get("stopDate", "").isoformat() if resp.get("stopDate") else None
            if "output" in resp:
                result["output"] = json.loads(resp["output"])
            if "error" in resp:
                result["error"] = resp["error"]
                result["cause"] = resp.get("cause", "")
        return {"statusCode": 200, "body": json.dumps(result, default=str)}

    # Mode 4: Direct invoke with base64-encoded PDF (single Lambda)
    if "pdf_base64" in event:
        bucket = event.get("bucket", BUCKET)
        filename = event.get("filename", "direct-upload.pdf")

        pdf_data = base64.b64decode(event["pdf_base64"])
        key = f"uploads/{filename}"

        s3.put_object(Bucket=bucket, Key=key, Body=pdf_data, ContentType="application/pdf")
        print(f"Uploaded {len(pdf_data)} bytes to s3://{bucket}/{key}")

        result = _process_pdf(bucket, key)
        return {"statusCode": 200, "body": json.dumps(result)}

    # Mode 5: Get index from S3
    if "get_index" in event:
        bucket = event.get("bucket", BUCKET)
        key = event["get_index"]
        obj = s3.get_object(Bucket=bucket, Key=key)
        content = obj["Body"].read().decode("utf-8")
        return {"statusCode": 200, "body": content}

    # Mode 6: List indexes
    if event.get("list_indexes"):
        bucket = event.get("bucket", BUCKET)
        resp = s3.list_objects_v2(Bucket=bucket, Prefix=OUTPUT_PREFIX)
        keys = [o["Key"] for o in resp.get("Contents", [])]
        return {"statusCode": 200, "body": json.dumps(keys)}

    return {"statusCode": 400, "body": "Unsupported event format"}

