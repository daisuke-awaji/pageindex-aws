"""Tests for assemble.py — logic and handler."""
import copy
import json
from unittest.mock import patch, MagicMock

import pytest


# ── Pure logic tests ──────────────────────────────────────────────

class TestApplySummaries:
    """Tests for _apply_summaries (pure function, no mocking)."""

    def _apply(self, structure, summaries_map):
        from assemble import _apply_summaries
        _apply_summaries(structure, summaries_map)

    def test_flat_structure(self, sample_structure):
        s = copy.deepcopy(sample_structure)
        self._apply(s, {"0000": "sum0", "0003": "sum3"})
        assert s[0]["summary"] == "sum0"
        assert s[1]["summary"] == "sum3"

    def test_nested_nodes(self, sample_structure):
        s = copy.deepcopy(sample_structure)
        self._apply(s, {"0001": "bg-sum", "0002": "obj-sum"})
        assert s[0]["nodes"][0]["summary"] == "bg-sum"
        assert s[0]["nodes"][1]["summary"] == "obj-sum"

    def test_all_nodes(self, sample_structure, sample_summaries):
        s = copy.deepcopy(sample_structure)
        smap = {item["nodeId"]: item["summary"] for item in sample_summaries}
        self._apply(s, smap)
        assert s[0]["summary"] == "Introduction summary"
        assert s[0]["nodes"][0]["summary"] == "Background summary"
        assert s[0]["nodes"][1]["summary"] == "Objectives summary"
        assert s[1]["summary"] == "Conclusion summary"

    def test_unmatched_id_ignored(self, sample_structure):
        s = copy.deepcopy(sample_structure)
        self._apply(s, {"9999": "orphan summary"})
        assert "summary" not in s[0]
        assert "summary" not in s[1]

    def test_empty_summaries_map(self, sample_structure):
        s = copy.deepcopy(sample_structure)
        original = copy.deepcopy(s)
        self._apply(s, {})
        assert s[0].get("summary") is None or "summary" not in s[0]

    def test_empty_structure(self):
        s = []
        self._apply(s, {"0000": "sum"})
        assert s == []

    def test_partial_match(self, sample_structure):
        s = copy.deepcopy(sample_structure)
        self._apply(s, {"0000": "only-root"})
        assert s[0]["summary"] == "only-root"
        assert "summary" not in s[0]["nodes"][0]


# ── Handler tests ─────────────────────────────────────────────────

class TestAssembleHandler:
    """Tests for assemble.lambda_handler with mocked S3 and LLM."""

    @patch("assemble.generate_doc_description", return_value="A test document.")
    @patch("assemble.s3")
    def test_handler_merges_summaries_and_saves(
        self, mock_s3, mock_gen_desc, mock_context, sample_structure, sample_summaries
    ):
        body_bytes = json.dumps(sample_structure).encode("utf-8")
        mock_s3.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=body_bytes))
        }
        mock_s3.list_objects_v2.return_value = {"Contents": []}

        from assemble import lambda_handler

        event = {
            "bucket": "test-bucket",
            "sourceKey": "uploads/test.pdf",
            "docName": "test.pdf",
            "tmpPrefix": "tmp/abc",
            "summaries": sample_summaries,
        }

        result = lambda_handler(event, mock_context)

        assert result["nodeCount"] == 4
        assert result["indexKey"] == "indexes/test_structure.json"
        mock_s3.put_object.assert_called_once()
        saved_body = mock_s3.put_object.call_args[1]["Body"]
        saved = json.loads(saved_body)
        assert saved["doc_description"] == "A test document."
        assert saved["doc_name"] == "test.pdf"

    @patch("assemble.generate_doc_description", return_value="desc")
    @patch("assemble.s3")
    def test_handler_empty_summaries(self, mock_s3, mock_gen_desc, mock_context):
        structure = [{"title": "Intro", "node_id": "0000", "start_index": 1, "end_index": 1, "text": "t"}]
        mock_s3.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=json.dumps(structure).encode()))
        }
        mock_s3.list_objects_v2.return_value = {"Contents": []}

        from assemble import lambda_handler

        result = lambda_handler(
            {"bucket": "b", "sourceKey": "uploads/x.pdf", "tmpPrefix": "tmp/x", "summaries": []},
            mock_context,
        )
        assert result["nodeCount"] == 0

    @patch("assemble.generate_doc_description", return_value="desc")
    @patch("assemble.s3")
    def test_handler_cleanup_called(self, mock_s3, mock_gen_desc, mock_context):
        structure = [{"title": "A", "node_id": "0000", "start_index": 1, "end_index": 1, "text": "t"}]
        mock_s3.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=json.dumps(structure).encode()))
        }
        mock_s3.list_objects_v2.return_value = {
            "Contents": [{"Key": "tmp/x/pages.json"}, {"Key": "tmp/x/structure.json"}]
        }

        from assemble import lambda_handler

        lambda_handler(
            {"bucket": "b", "sourceKey": "uploads/x.pdf", "tmpPrefix": "tmp/x", "summaries": []},
            mock_context,
        )
        assert mock_s3.delete_object.call_count == 2
