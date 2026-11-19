import unittest
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

from court_transcription import s3_utils


class FakeStreamingBody:
    def __init__(self, chunks: list[bytes]):
        self.chunks = chunks
        self.closed = False

    def iter_chunks(self, chunk_size: int):
        self.chunk_size = chunk_size
        yield from self.chunks

    def close(self):
        self.closed = True


class S3UtilsTests(unittest.TestCase):
    def test_key_from_storage_uri_accepts_uri_or_key(self):
        self.assertEqual(
            s3_utils.key_from_storage_uri("s3://court-bucket/transcripts/case/audio.wav"),
            "transcripts/case/audio.wav",
        )
        self.assertEqual(s3_utils.key_from_storage_uri("/transcripts/case/audio.wav"), "transcripts/case/audio.wav")

    @patch("court_transcription.s3_utils._s3_client")
    def test_upload_bytes_uses_bucket_key_content_type_and_returns_s3_uri(self, client_factory):
        client = client_factory.return_value

        uri = s3_utils.upload_bytes(
            "court-bucket",
            "/transcripts/case/transcript.txt",
            b"court text",
            content_type="text/plain",
            region="ap-south-1",
        )

        client_factory.assert_called_once_with("ap-south-1")
        client.put_object.assert_called_once_with(
            Bucket="court-bucket",
            Key="transcripts/case/transcript.txt",
            Body=b"court text",
            ContentType="text/plain",
        )
        self.assertEqual(uri, "s3://court-bucket/transcripts/case/transcript.txt")

    @patch("court_transcription.s3_utils._s3_client")
    def test_list_directory_entries_returns_immediate_objects_and_directories(self, client_factory):
        paginator = MagicMock()
        paginator.paginate.return_value = [
            {
                "Contents": [
                    {"Key": "transcripts/court/2026-06-11/transcript.txt", "Size": 10},
                ],
                "CommonPrefixes": [
                    {"Prefix": "transcripts/court/2026-06-11/case-001/"},
                ],
            }
        ]
        client_factory.return_value.get_paginator.return_value = paginator

        entries = s3_utils.list_s3_directory_entries(
            "court-bucket",
            "transcripts/court/2026-06-11",
            region="ap-south-1",
        )

        paginator.paginate.assert_called_once_with(
            Bucket="court-bucket",
            Prefix="transcripts/court/2026-06-11/",
            Delimiter="/",
        )
        self.assertEqual(entries[0]["Type"], "object")
        self.assertEqual(entries[1]["Type"], "directory")
        self.assertEqual(entries[1]["Key"], "transcripts/court/2026-06-11/case-001")

    @patch("court_transcription.s3_utils._s3_client")
    def test_stream_object_forwards_range_and_closes_body(self, client_factory):
        body = FakeStreamingBody([b"one", b"two"])
        client_factory.return_value.get_object.return_value = {
            "Body": body,
            "ContentRange": "bytes 0-5/10",
            "ContentLength": 6,
            "ResponseMetadata": {"HTTPStatusCode": 206},
        }

        chunks, status_code, headers = s3_utils.stream_s3_object_range(
            "court-bucket",
            "transcripts/case/audio.wav",
            range_header="bytes=0-5",
            region="ap-south-1",
        )

        client_factory.return_value.get_object.assert_called_once_with(
            Bucket="court-bucket",
            Key="transcripts/case/audio.wav",
            Range="bytes=0-5",
        )
        self.assertEqual(list(chunks), [b"one", b"two"])
        self.assertTrue(body.closed)
        self.assertEqual(status_code, 206)
        self.assertEqual(headers["Content-Range"], "bytes 0-5/10")
        self.assertEqual(headers["Content-Length"], "6")

    @patch("court_transcription.s3_utils._s3_client")
    def test_missing_object_returns_none(self, client_factory):
        client_factory.return_value.get_object.side_effect = ClientError(
            {
                "Error": {"Code": "NoSuchKey", "Message": "missing"},
                "ResponseMetadata": {"HTTPStatusCode": 404},
            },
            "GetObject",
        )

        self.assertIsNone(s3_utils.get_s3_object_bytes("court-bucket", "missing.txt"))


if __name__ == "__main__":
    unittest.main()
