"""
Phase 1: PDF Parse + Structure Building
- Downloads PDF from S3
- Parses pages, detects TOC, builds tree structure
- Saves intermediate data (page_list, structure) to S3
- Returns compact node list for Map state (no text, avoids 256KB limit)
"""
import asyncio
import json
import os
import tempfile

os.makedirs("/tmp/logs", exist_ok=True)
os.makedirs("/tmp/results", exist_ok=True)
os.chdir("/tmp")

import boto3

from pageindex.page_index import tree_parser
from pageindex.utils import (
    ConfigLoader,
    JsonLogger,
    add_node_text,
    get_page_tokens,
    get_pdf_name,
    structure_to_list,
    write_node_id,
)

s3 = boto3.client("s3")
MODEL = os.environ.get("PAGEINDEX_MODEL", "bedrock/jp.anthropic.claude-haiku-4-5-20251001-v1:0")


def lambda_handler(event, context):
    bucket = event["bucket"]
    key = event["key"]
    execution_id = event.get("executionId", context.aws_request_id)

    print(f"[Phase1] Processing s3://{bucket}/{key}")

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        s3.download_file(bucket, key, tmp.name)
        tmp_path = tmp.name

    try:
        doc_name = get_pdf_name(tmp_path)
        opt = ConfigLoader().load({"model": MODEL})
        logger = JsonLogger(tmp_path)

        page_list = get_page_tokens(tmp_path, model=opt.model)
        print(f"[Phase1] {len(page_list)} pages, {sum(p[1] for p in page_list)} tokens")

        async def build_structure():
            structure = await tree_parser(page_list, opt, doc=tmp_path, logger=logger)
            write_node_id(structure)
            add_node_text(structure, page_list)
            return structure

        structure = asyncio.run(build_structure())
    finally:
        os.unlink(tmp_path)

    tmp_prefix = f"tmp/{execution_id}"

    # Save page_list to S3 (needed by summarize-node to extract page text)
    pages_data = [{"text": p[0], "tokens": p[1]} for p in page_list]
    s3.put_object(
        Bucket=bucket,
        Key=f"{tmp_prefix}/pages.json",
        Body=json.dumps(pages_data, ensure_ascii=False),
        ContentType="application/json",
    )

    # Save full structure (with text) to S3
    s3.put_object(
        Bucket=bucket,
        Key=f"{tmp_prefix}/structure.json",
        Body=json.dumps(structure, ensure_ascii=False),
        ContentType="application/json",
    )

    # Build compact node list for Map state (no text → fits 256KB limit)
    nodes = structure_to_list(structure)
    node_items = [
        {
            "nodeId": n.get("node_id", ""),
            "title": n.get("title", ""),
            "startIndex": n.get("start_index"),
            "endIndex": n.get("end_index"),
        }
        for n in nodes
    ]

    print(f"[Phase1] Structure built: {len(node_items)} nodes")

    return {
        "bucket": bucket,
        "sourceKey": key,
        "docName": doc_name,
        "tmpPrefix": tmp_prefix,
        "nodeCount": len(node_items),
        "nodes": node_items,
    }

