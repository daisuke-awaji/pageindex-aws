"""Tests for handler.py — routing and handler modes."""
import json
from unittest.mock import patch, MagicMock

import pytest


class TestHandlerRouting:
    """Test that events are routed to the correct mode."""

    @patch("handler._process_pdf", return_value={"source": "s3://b/k", "index": "s3://b/i"})
    @patch("handler.s3")
    def test_mode0_sfn_single_path(self, mock_s3, mock_process, mock_context):
        from handler import lambda_handler

        result = lambda_handler(
            {"_source": "stepfunctions-single", "bucket": "b", "key": "uploads/test.pdf"},
            mock_context,
        )
        mock_process.assert_called_once_with("b", "uploads/test.pdf")
        assert result["statusCode"] == 200

    @patch("handler.boto3")
    @patch("handler.s3")
    def test_mode2_start_workflow(self, mock_s3, mock_boto3, mock_context):
        mock_sfn = MagicMock()
        mock_sfn.start_execution.return_value = {
            "executionArn": "arn:aws:states:us-east-1:123:execution:wf:exec-123"
        }
        mock_boto3.client.return_value = mock_sfn

        from handler import lambda_handler

        result = lambda_handler(
            {
                "start_workflow": "uploads/test.pdf",
                "bucket": "test-bucket",
                "workflow_arn": "arn:aws:states:us-east-1:123:stateMachine:wf",
            },
            mock_context,
        )
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert "executionArn" in body
        mock_sfn.start_execution.assert_called_once()

    @patch("handler.boto3")
    @patch("handler.s3")
    def test_mode3_check_execution(self, mock_s3, mock_boto3, mock_context):
        from datetime import datetime

        mock_sfn = MagicMock()
        mock_sfn.describe_execution.return_value = {
            "status": "SUCCEEDED",
            "startDate": datetime(2026, 1, 1, 0, 0, 0),
            "stopDate": datetime(2026, 1, 1, 0, 1, 0),
            "output": '{"result": "ok"}',
        }
        mock_boto3.client.return_value = mock_sfn

        from handler import lambda_handler

        result = lambda_handler(
            {"check_execution": "arn:aws:states:us-east-1:123:execution:wf:exec-123"},
            mock_context,
        )
        body = json.loads(result["body"])
        assert body["status"] == "SUCCEEDED"
        assert body["output"] == {"result": "ok"}

    @patch("handler.s3")
    def test_mode5_get_index(self, mock_s3, mock_context):
        mock_s3.get_object.return_value = {
            "Body": MagicMock(
                read=MagicMock(return_value=b'{"structure": []}')
            )
        }

        from handler import lambda_handler

        result = lambda_handler({"get_index": "indexes/test.json"}, mock_context)
        assert result["statusCode"] == 200
        assert result["body"] == '{"structure": []}'

    @patch("handler.s3")
    def test_mode6_list_indexes(self, mock_s3, mock_context):
        mock_s3.list_objects_v2.return_value = {
            "Contents": [
                {"Key": "indexes/a.json"},
                {"Key": "indexes/b.json"},
            ]
        }

        from handler import lambda_handler

        result = lambda_handler({"list_indexes": True}, mock_context)
        body = json.loads(result["body"])
        assert len(body) == 2
        assert "indexes/a.json" in body

    @patch("handler.s3")
    def test_unsupported_event_returns_400(self, mock_s3, mock_context):
        from handler import lambda_handler

        result = lambda_handler({}, mock_context)
        assert result["statusCode"] == 400

    @patch("handler.s3")
    def test_start_workflow_with_pdf_upload(self, mock_s3, mock_context):
        import base64

        mock_sfn = MagicMock()
        mock_sfn.start_execution.return_value = {"executionArn": "arn:test:exec"}

        with patch("handler.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_sfn

            from handler import lambda_handler

            result = lambda_handler(
                {
                    "start_workflow": "uploads/test.pdf",
                    "pdf_base64": base64.b64encode(b"fake-pdf").decode(),
                    "bucket": "b",
                    "workflow_arn": "arn:test:wf",
                },
                mock_context,
            )
        assert result["statusCode"] == 200
        mock_s3.put_object.assert_called_once()

    @patch("handler._process_pdf", return_value={"source": "s", "index": "i"})
    @patch("handler.s3")
    def test_mode4_pdf_base64_direct(self, mock_s3, mock_process, mock_context):
        import base64

        from handler import lambda_handler

        result = lambda_handler(
            {
                "pdf_base64": base64.b64encode(b"fake-pdf").decode(),
                "bucket": "b",
                "filename": "test.pdf",
            },
            mock_context,
        )
        assert result["statusCode"] == 200
        mock_s3.put_object.assert_called_once()
        mock_process.assert_called_once()

    @patch("handler._process_pdf", return_value={"source": "s", "index": "i"})
    @patch("handler.s3")
    def test_start_workflow_takes_priority_over_pdf_base64(self, mock_s3, mock_process, mock_context):
        """When both start_workflow and pdf_base64 are present, start_workflow wins."""
        import base64

        mock_sfn = MagicMock()
        mock_sfn.start_execution.return_value = {"executionArn": "arn:test:exec"}

        with patch("handler.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_sfn

            from handler import lambda_handler

            result = lambda_handler(
                {
                    "start_workflow": "uploads/test.pdf",
                    "pdf_base64": base64.b64encode(b"fake-pdf").decode(),
                    "bucket": "b",
                    "workflow_arn": "arn:test:wf",
                },
                mock_context,
            )

        mock_process.assert_not_called()  # should NOT go to pdf_base64 mode
        body = json.loads(result["body"])
        assert "executionArn" in body
