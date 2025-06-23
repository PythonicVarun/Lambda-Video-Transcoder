import json
import os
import unittest
from unittest.mock import patch, MagicMock

import boto3
from moto import mock_aws

# Add the src directory to the Python path
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from transcoder.app import app, lambda_handler


class TranscoderTestCase(unittest.TestCase):
    def setUp(self):
        """Set up test client and mock AWS services."""
        app.config["TESTING"] = True
        self.client = app.test_client()

        # Mock environment variables
        self.mock_env = patch.dict(
            os.environ,
            {
                "BUCKET_NAME": "test-bucket",
                "LAMBDA_FUNCTION_URL": "http://localhost:8000",
                "GENERATE_DASH": "true",
                "GENERATE_SUBTITLES": "true",
            },
        )
        self.mock_env.start()

        # Start moto AWS mock
        self.mock_aws = mock_aws()
        self.mock_aws.start()

        # Create a mock S3 bucket
        self.s3 = boto3.client("s3", region_name="us-east-1")
        self.s3.create_bucket(Bucket="test-bucket")

        # Mock subprocess calls for ffmpeg/ffprobe
        self.mock_subprocess = patch("transcoder.app.subprocess")
        self.mock_subprocess_instance = self.mock_subprocess.start()

    def tearDown(self):
        """Stop mocking."""
        self.mock_aws.stop()
        self.mock_env.stop()
        self.mock_subprocess.stop()

    def test_index_route(self):
        """Test the index route."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"<h2 class=\"text-2xl font-bold text-[var(--text-primary)] mb-6\">Upload Video</h2>", response.data)

    @patch("transcoder.app.process_video")
    def test_lambda_handler_s3_upload(self, mock_process_video):
        """Test lambda_handler for S3 video upload event."""
        event = {
            "Records": [
                {
                    "s3": {
                        "bucket": {"name": "test-bucket"},
                        "object": {"key": "uploads/test.mp4"},
                    }
                }
            ]
        }
        lambda_handler(event, {})
        mock_process_video.assert_called_once_with(event)

    @patch("transcoder.app.process_transcription_result")
    def test_lambda_handler_s3_transcription(self, mock_process_transcription):
        """Test lambda_handler for S3 transcription result event."""
        event = {
            "Records": [
                {
                    "s3": {
                        "bucket": {"name": "test-bucket"},
                        "object": {
                            "key": "processed/some_id/transcripts/job.json"
                        },
                    }
                }
            ]
        }
        lambda_handler(event, {})
        mock_process_transcription.assert_called_once_with(event)

    @patch("serverless_wsgi.handle_request")
    def test_lambda_handler_api_gateway(self, mock_handle_request):
        """Test lambda_handler for API Gateway event."""
        event = {"requestContext": {"http": {"method": "GET", "path": "/"}}}
        lambda_handler(event, {})
        mock_handle_request.assert_called_once()


if __name__ == "__main__":
    unittest.main()
