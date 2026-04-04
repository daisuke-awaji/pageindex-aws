"""Tests for summarize_node.py — logic and handler."""
import json
from unittest.mock import patch, MagicMock, AsyncMock

import pytest


# ── Pure logic tests ──────────────────────────────────────────────

class TestGetTextForPages:
    """Tests for _get_text_for_pages (pure function)."""

    def _func(self, pages, start, end):
        from summarize_node import _get_text_for_pages
        return _get_text_for_pages(pages, start, end)

    def test_single_page(self, sample_pages):
        result = self._func(sample_pages, 1, 1)
        assert result == "Page 1 text content"

    def test_multiple_pages(self, sample_pages):
        result = self._func(sample_pages, 2, 3)
        assert "Page 2 text content" in result
        assert "Page 3 text content" in result

    def test_all_pages(self, sample_pages):
        result = self._func(sample_pages, 1, 4)
        for i in range(1, 5):
            assert f"Page {i} text content" in result

    def test_end_exceeds_length(self, sample_pages):
        result = self._func(sample_pages, 3, 100)
        assert "Page 3 text content" in result
        assert "Page 4 text content" in result

    def test_empty_pages(self):
        result = self._func([], 1, 1)
        assert result == ""

    def test_start_equals_end(self, sample_pages):
        result = self._func(sample_pages, 2, 2)
        assert result == "Page 2 text content"


# ── Handler tests ─────────────────────────────────────────────────

class TestSummarizeNodeHandler:
    """Tests for summarize_node.lambda_handler with mocked S3 and LLM."""

    @patch("summarize_node.s3")
    @patch("summarize_node._generate_summary", new_callable=AsyncMock, return_value="Test summary")
    def test_handler_normal(self, mock_gen, mock_s3, mock_context, sample_pages):
        mock_s3.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=json.dumps(sample_pages).encode()))
        }

        from summarize_node import lambda_handler, _pages_cache
        _pages_cache.clear()

        result = lambda_handler(
            {
                "nodeId": "0001",
                "title": "Test",
                "startIndex": 1,
                "endIndex": 2,
                "bucket": "test-bucket",
                "tmpPrefix": "tmp/abc",
            },
            mock_context,
        )
        assert result["nodeId"] == "0001"
        assert result["summary"] == "Test summary"
        mock_gen.assert_called_once()

    @patch("summarize_node.s3")
    def test_handler_no_start_index(self, mock_s3, mock_context):
        from summarize_node import lambda_handler

        result = lambda_handler(
            {"nodeId": "0001", "startIndex": None, "endIndex": None, "bucket": "b", "tmpPrefix": "t"},
            mock_context,
        )
        assert result["nodeId"] == "0001"
        assert result["summary"] == ""
        mock_s3.get_object.assert_not_called()

    @patch("summarize_node.s3")
    @patch("summarize_node._generate_summary", new_callable=AsyncMock, return_value="Cached result")
    def test_handler_uses_page_cache(self, mock_gen, mock_s3, mock_context, sample_pages):
        mock_s3.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=json.dumps(sample_pages).encode()))
        }

        from summarize_node import lambda_handler, _pages_cache
        _pages_cache.clear()

        # First call: loads from S3
        lambda_handler(
            {"nodeId": "0001", "startIndex": 1, "endIndex": 1, "bucket": "b", "tmpPrefix": "t"},
            mock_context,
        )
        assert mock_s3.get_object.call_count == 1

        # Second call: uses cache
        lambda_handler(
            {"nodeId": "0002", "startIndex": 2, "endIndex": 2, "bucket": "b", "tmpPrefix": "t"},
            mock_context,
        )
        assert mock_s3.get_object.call_count == 1  # not called again
