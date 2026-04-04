"""Tests for count_pages.py — logic and handler."""
import json
import os
import tempfile
from unittest.mock import patch, MagicMock

import pytest


# ── Pure logic tests ──────────────────────────────────────────────

class TestRoutingLogic:
    """Test the page count → strategy routing logic."""

    @pytest.mark.parametrize(
        "page_count, threshold, expected",
        [
            (8, 50, "single"),
            (50, 50, "single"),
            (51, 50, "stepfunctions"),
            (100, 50, "stepfunctions"),
            (1, 50, "single"),
            (10, 10, "single"),
            (11, 10, "stepfunctions"),
        ],
    )
    def test_strategy_selection(self, page_count, threshold, expected):
        strategy = "single" if page_count <= threshold else "stepfunctions"
        assert strategy == expected


# ── Handler tests ─────────────────────────────────────────────────

class TestCountPagesHandler:
    """Tests for count_pages.lambda_handler with mocked S3."""

    def _invoke(self, mock_s3, pdf_bytes, mock_context, bucket="test-bucket", key="uploads/test.pdf"):
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.write(pdf_bytes)
        tmp.close()

        def fake_download(b, k, path):
            import shutil
            shutil.copy(tmp.name, path)

        mock_s3.download_file.side_effect = fake_download

        from count_pages import lambda_handler

        result = lambda_handler({"bucket": bucket, "key": key}, mock_context)
        os.unlink(tmp.name)
        return result

    @patch("count_pages.s3")
    def test_small_pdf_returns_single(self, mock_s3, mock_context, small_pdf_bytes):
        result = self._invoke(mock_s3, small_pdf_bytes, mock_context)
        assert result["strategy"] == "single"
        assert result["pageCount"] == 8
        assert result["bucket"] == "test-bucket"
        assert result["key"] == "uploads/test.pdf"

    @patch("count_pages.s3")
    def test_large_pdf_returns_stepfunctions(self, mock_s3, mock_context, large_pdf_bytes):
        result = self._invoke(mock_s3, large_pdf_bytes, mock_context)
        assert result["strategy"] == "stepfunctions"
        assert result["pageCount"] == 60

    @patch("count_pages.s3")
    def test_boundary_pdf_returns_single(self, mock_s3, mock_context, boundary_pdf_bytes):
        result = self._invoke(mock_s3, boundary_pdf_bytes, mock_context)
        assert result["strategy"] == "single"
        assert result["pageCount"] == 50

    @patch("count_pages.s3")
    def test_custom_threshold(self, mock_s3, mock_context, small_pdf_bytes):
        """PAGE_THRESHOLD=5 makes an 8-page PDF route to stepfunctions."""
        import count_pages
        original_threshold = count_pages.PAGE_THRESHOLD
        count_pages.PAGE_THRESHOLD = 5
        try:
            result = self._invoke(mock_s3, small_pdf_bytes, mock_context)
            assert result["strategy"] == "stepfunctions"
            assert result["pageCount"] == 8
        finally:
            count_pages.PAGE_THRESHOLD = original_threshold
