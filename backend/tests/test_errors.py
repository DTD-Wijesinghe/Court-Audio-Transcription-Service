import errno
import unittest

from botocore.exceptions import ClientError, EndpointConnectionError, NoCredentialsError

from court_transcription.errors import classify_exception, error_payload


class SystemErrorTests(unittest.TestCase):
    def test_server_disk_full(self):
        error = classify_exception(OSError(errno.ENOSPC, "No space left on device"))
        self.assertEqual(error.code, "SRV-0001")
        self.assertEqual(error.http_status, 507)

    def test_aws_credentials_missing(self):
        self.assertEqual(classify_exception(NoCredentialsError()).code, "AWS-0001")

    def test_aws_access_denied(self):
        exc = ClientError({"Error": {"Code": "AccessDenied", "Message": "secret provider text"}}, "ListObjectsV2")
        self.assertEqual(classify_exception(exc).code, "AWS-0002")

    def test_aws_connection_failure_is_retryable(self):
        error = classify_exception(EndpointConnectionError(endpoint_url="https://s3.invalid"))
        self.assertEqual(error.code, "AWS-0003")
        self.assertTrue(error.retryable)

    def test_public_payload_has_request_id_and_no_internal_exception(self):
        info = classify_exception(RuntimeError("database password leaked here"))
        payload = error_payload(info, "request-123")
        self.assertEqual(payload["error"]["requestId"], "request-123")
        self.assertNotIn("password", str(payload).lower())


if __name__ == "__main__":
    unittest.main()
