#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUTPUT_DIR="${1:?Usage: build.sh <output-dir>}"

echo "==> Installing dependencies to $OUTPUT_DIR"
pip3 install \
  --platform manylinux2014_aarch64 \
  --python-version 3.12 \
  --only-binary :all: \
  --target "$OUTPUT_DIR" \
  pymupdf==1.26.0 PyPDF2==3.0.1 litellm pyyaml python-dotenv 2>&1 | tail -3

echo "==> Cloning PageIndex source"
TMPDIR=$(mktemp -d)
git clone --depth 1 https://github.com/VectifyAI/PageIndex.git "$TMPDIR/PageIndex" 2>/dev/null
cp -r "$TMPDIR/PageIndex/pageindex" "$OUTPUT_DIR/pageindex"
rm -rf "$TMPDIR"

echo "==> Copying handlers"
cp "$SCRIPT_DIR/handler.py" "$OUTPUT_DIR/"
cp "$SCRIPT_DIR/parse_and_structure.py" "$OUTPUT_DIR/"
cp "$SCRIPT_DIR/summarize_node.py" "$OUTPUT_DIR/"
cp "$SCRIPT_DIR/assemble.py" "$OUTPUT_DIR/"
cp "$SCRIPT_DIR/count_pages.py" "$OUTPUT_DIR/"

echo "==> Trimming unnecessary files"
find "$OUTPUT_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "$OUTPUT_DIR" -type d -name "*.dist-info" -exec rm -rf {} + 2>/dev/null || true
find "$OUTPUT_DIR" -type d -name "tests" -exec rm -rf {} + 2>/dev/null || true

TOTAL_SIZE=$(du -sm "$OUTPUT_DIR" | cut -f1)
echo "==> Total size: ${TOTAL_SIZE}MB (Lambda limit: 250MB)"

