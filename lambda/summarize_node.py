"""
Phase 2 (Map item): Summarize a single node
- Reads page text from S3 (pages.json)
- Calls Bedrock to generate summary
- Returns { nodeId, summary }
"""
import asyncio
import json
import os

os.makedirs("/tmp/logs", exist_ok=True)
os.chdir("/tmp")

import boto3

from pageindex.utils import llm_acompletion

s3 = boto3.client("s3")
MODEL = os.environ.get("PAGEINDEX_MODEL", "bedrock/jp.anthropic.claude-haiku-4-5-20251001-v1:0")

_pages_cache: dict[str, list] = {}


def _load_pages(bucket: str, pages_key: str) -> list:
    cache_key = f"{bucket}/{pages_key}"
    if cache_key not in _pages_cache:
        obj = s3.get_object(Bucket=bucket, Key=pages_key)
        _pages_cache[cache_key] = json.loads(obj["Body"].read().decode("utf-8"))
    return _pages_cache[cache_key]


def _get_text_for_pages(pages: list, start_index: int, end_index: int) -> str:
    texts = []
    for i in range(start_index - 1, min(end_index, len(pages))):
        texts.append(pages[i]["text"])
    return "\n".join(texts)


async def _generate_summary(text: str, model: str) -> str:
    prompt = (
        "You are given a part of a document, your task is to generate a description "
        "of the partial document about what are main points covered in the partial document.\n\n"
        f"Partial Document Text: {text}\n\n"
        "Directly return the description, do not include any other text."
    )
    return await llm_acompletion(model, prompt)


def lambda_handler(event, context):
    node_id = event["nodeId"]
    title = event.get("title", "")
    start_index = event.get("startIndex")
    end_index = event.get("endIndex")
    bucket = event["bucket"]
    tmp_prefix = event["tmpPrefix"]

    if not start_index or not end_index:
        return {"nodeId": node_id, "summary": ""}

    pages = _load_pages(bucket, f"{tmp_prefix}/pages.json")
    text = _get_text_for_pages(pages, start_index, end_index)

    if not text.strip():
        print(f"[Summarize] Node {node_id} ({title}): no text, skipping")
        return {"nodeId": node_id, "summary": ""}

    print(f"[Summarize] Node {node_id} ({title}): pages {start_index}-{end_index}, {len(text)} chars")
    summary = asyncio.run(_generate_summary(text, MODEL))
    print(f"[Summarize] Node {node_id}: summary {len(summary)} chars")

    return {"nodeId": node_id, "summary": summary}

