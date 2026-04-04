import io
import os
import sys
import json
import types
import pytest
from unittest.mock import MagicMock

# Add lambda directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("PAGEINDEX_MODEL", "bedrock/test-model")
os.environ.setdefault("OUTPUT_PREFIX", "indexes/")
os.environ.setdefault("BUCKET_NAME", "test-bucket")
os.environ.setdefault("PAGE_THRESHOLD", "50")
os.environ.setdefault("WORKFLOW_ARN", "arn:aws:states:us-east-1:123456789012:stateMachine:test")

# Mock pageindex module tree before any handler imports it
_pageindex = types.ModuleType("pageindex")
_pageindex.page_index_main = MagicMock(return_value={"doc_name": "test", "structure": []})

_pageindex_utils = types.ModuleType("pageindex.utils")
_pageindex_utils.ConfigLoader = MagicMock()
_pageindex_utils.create_clean_structure_for_description = MagicMock(return_value={})
_pageindex_utils.format_structure = MagicMock(side_effect=lambda s, **kw: s)
_pageindex_utils.generate_doc_description = MagicMock(return_value="Mock description")
_pageindex_utils.remove_structure_text = MagicMock(side_effect=lambda s: s)
_pageindex_utils.structure_to_list = MagicMock(return_value=[])
_pageindex_utils.llm_acompletion = MagicMock()
_pageindex_utils.JsonLogger = MagicMock()
_pageindex_utils.add_node_text = MagicMock()
_pageindex_utils.get_page_tokens = MagicMock(return_value=[])
_pageindex_utils.get_pdf_name = MagicMock(return_value="test.pdf")
_pageindex_utils.write_node_id = MagicMock()

_pageindex_page_index = types.ModuleType("pageindex.page_index")
_pageindex_page_index.tree_parser = MagicMock()

_pageindex_page_index_md = types.ModuleType("pageindex.page_index_md")
_pageindex_page_index_md.md_to_tree = MagicMock()

_pageindex_client = types.ModuleType("pageindex.client")
_pageindex_client.PageIndexClient = MagicMock()

_pageindex_retrieve = types.ModuleType("pageindex.retrieve")
_pageindex_retrieve.get_document = MagicMock()
_pageindex_retrieve.get_document_structure = MagicMock()
_pageindex_retrieve.get_page_content = MagicMock()

sys.modules["pageindex"] = _pageindex
sys.modules["pageindex.utils"] = _pageindex_utils
sys.modules["pageindex.page_index"] = _pageindex_page_index
sys.modules["pageindex.page_index_md"] = _pageindex_page_index_md
sys.modules["pageindex.client"] = _pageindex_client
sys.modules["pageindex.retrieve"] = _pageindex_retrieve

# Also mock litellm (imported by pageindex.utils at module level)
if "litellm" not in sys.modules:
    sys.modules["litellm"] = MagicMock()
if "pymupdf" not in sys.modules:
    sys.modules["pymupdf"] = MagicMock()
if "dotenv" not in sys.modules:
    sys.modules["dotenv"] = MagicMock()


@pytest.fixture
def mock_context():
    ctx = MagicMock()
    ctx.aws_request_id = "test-request-id"
    ctx.function_name = "test-function"
    return ctx


def _make_pdf(num_pages: int) -> bytes:
    from fpdf import FPDF
    pdf = FPDF()
    for i in range(num_pages):
        pdf.add_page()
        pdf.set_font("Helvetica", size=12)
        pdf.cell(0, 10, f"Page {i + 1} content for testing.", new_x="LMARGIN", new_y="NEXT")
    return pdf.output()


@pytest.fixture
def small_pdf_bytes():
    return _make_pdf(8)


@pytest.fixture
def large_pdf_bytes():
    return _make_pdf(60)


@pytest.fixture
def boundary_pdf_bytes():
    return _make_pdf(50)


@pytest.fixture
def sample_structure():
    return [
        {
            "title": "Introduction",
            "node_id": "0000",
            "start_index": 1,
            "end_index": 2,
            "text": "Some intro text",
            "nodes": [
                {
                    "title": "Background",
                    "node_id": "0001",
                    "start_index": 1,
                    "end_index": 1,
                    "text": "Background text",
                },
                {
                    "title": "Objectives",
                    "node_id": "0002",
                    "start_index": 2,
                    "end_index": 2,
                    "text": "Objectives text",
                },
            ],
        },
        {
            "title": "Conclusion",
            "node_id": "0003",
            "start_index": 3,
            "end_index": 3,
            "text": "Conclusion text",
        },
    ]


@pytest.fixture
def sample_summaries():
    return [
        {"nodeId": "0000", "summary": "Introduction summary"},
        {"nodeId": "0001", "summary": "Background summary"},
        {"nodeId": "0002", "summary": "Objectives summary"},
        {"nodeId": "0003", "summary": "Conclusion summary"},
    ]


@pytest.fixture
def sample_pages():
    return [
        {"text": "Page 1 text content", "tokens": 10},
        {"text": "Page 2 text content", "tokens": 12},
        {"text": "Page 3 text content", "tokens": 8},
        {"text": "Page 4 text content", "tokens": 15},
    ]
