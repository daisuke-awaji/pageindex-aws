"""
Phase 3: Assemble final index
- Reads structure from S3
- Merges summaries from Map output
- Generates document description
- Saves final index JSON to S3
- Cleans up temporary data
"""
import json
import os

os.makedirs("/tmp/logs", exist_ok=True)
os.chdir("/tmp")

import boto3

from pageindex.utils import (
    ConfigLoader,
    create_clean_structure_for_description,
    format_structure,
    generate_doc_description,
    remove_structure_text,
    structure_to_list,
)

s3 = boto3.client("s3")
MODEL = os.environ.get("PAGEINDEX_MODEL", "bedrock/jp.anthropic.claude-haiku-4-5-20251001-v1:0")
OUTPUT_PREFIX = os.environ.get("OUTPUT_PREFIX", "indexes/")


def _apply_summaries(structure, summaries_map):
    """Apply summary map to tree structure nodes."""
    if isinstance(structure, dict):
        nid = structure.get("node_id", "")
        if nid in summaries_map:
            structure["summary"] = summaries_map[nid]
        if "nodes" in structure:
            _apply_summaries(structure["nodes"], summaries_map)
    elif isinstance(structure, list):
        for item in structure:
            _apply_summaries(item, summaries_map)


def _cleanup_tmp(bucket, prefix):
    """Delete temporary S3 objects."""
    try:
        resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
        for obj in resp.get("Contents", []):
            s3.delete_object(Bucket=bucket, Key=obj["Key"])
        print(f"[Assemble] Cleaned up {len(resp.get('Contents', []))} tmp objects")
    except Exception as e:
        print(f"[Assemble] Cleanup warning: {e}")


def lambda_handler(event, context):
    bucket = event["bucket"]
    source_key = event["sourceKey"]
    doc_name = event.get("docName", "unknown")
    tmp_prefix = event["tmpPrefix"]
    summaries = event.get("summaries", [])

    print(f"[Assemble] Merging {len(summaries)} summaries for {doc_name}")

    # Load structure from S3
    obj = s3.get_object(Bucket=bucket, Key=f"{tmp_prefix}/structure.json")
    structure = json.loads(obj["Body"].read().decode("utf-8"))

    # Build summary map and apply
    summaries_map = {}
    for s_item in summaries:
        if isinstance(s_item, dict) and s_item.get("nodeId"):
            summaries_map[s_item["nodeId"]] = s_item.get("summary", "")

    _apply_summaries(structure, summaries_map)
    remove_structure_text(structure)

    # Generate document description
    clean_structure = create_clean_structure_for_description(structure)
    doc_description = generate_doc_description(clean_structure, model=MODEL)

    # Format structure
    structure = format_structure(
        structure,
        order=["title", "node_id", "start_index", "end_index", "summary", "nodes"],
    )

    result = {
        "doc_name": doc_name,
        "doc_description": doc_description,
        "structure": structure,
    }

    # Save to S3
    base_name = os.path.splitext(os.path.basename(source_key))[0]
    output_key = f"{OUTPUT_PREFIX}{base_name}_structure.json"
    s3.put_object(
        Bucket=bucket,
        Key=output_key,
        Body=json.dumps(result, ensure_ascii=False, indent=2),
        ContentType="application/json",
    )
    print(f"[Assemble] Index saved to s3://{bucket}/{output_key}")

    # Cleanup temporary data
    _cleanup_tmp(bucket, tmp_prefix)

    return {
        "indexKey": output_key,
        "source": f"s3://{bucket}/{source_key}",
        "index": f"s3://{bucket}/{output_key}",
        "nodeCount": len(summaries_map),
    }
