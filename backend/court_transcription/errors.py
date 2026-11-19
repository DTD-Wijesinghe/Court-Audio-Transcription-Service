from __future__ import annotations

import errno
import socket
from dataclasses import dataclass
from typing import Any

from botocore.exceptions import (
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    NoCredentialsError,
    PartialCredentialsError,
)


@dataclass(frozen=True)
class SystemErrorInfo:
    code: str
    message: str
    category: str
    http_status: int
    retryable: bool = False


CATALOG: dict[str, SystemErrorInfo] = {
    "SYS-0001": SystemErrorInfo("SYS-0001", "An unexpected system error occurred. Please try again or contact support.", "system", 500),
    "NET-0001": SystemErrorInfo("NET-0001", "Data connection lost. Check your internet connection and try again.", "network", 503, True),
    "NET-0002": SystemErrorInfo("NET-0002", "The service connection timed out. Please try again.", "network", 504, True),
    "AUTH-0001": SystemErrorInfo("AUTH-0001", "Authentication is required. Please sign in again.", "authentication", 401),
    "AUTH-0002": SystemErrorInfo("AUTH-0002", "Your session is invalid or has expired. Please sign in again.", "authentication", 401),
    "AUTH-0003": SystemErrorInfo("AUTH-0003", "You do not have permission to perform this action.", "authorization", 403),
    "AUTH-0004": SystemErrorInfo("AUTH-0004", "The account is temporarily locked because of repeated sign-in attempts.", "authentication", 429, True),
    "AUTH-0005": SystemErrorInfo("AUTH-0005", "The account email address has not been verified.", "authentication", 403),
    "AUTH-0006": SystemErrorInfo("AUTH-0006", "An unexpected authentication error occurred.", "authentication", 500),
    "REQ-0001": SystemErrorInfo("REQ-0001", "The request contains invalid data. Check the entered values.", "validation", 400),
    "REQ-0002": SystemErrorInfo("REQ-0002", "The requested record or file was not found.", "not_found", 404),
    "REQ-0003": SystemErrorInfo("REQ-0003", "The uploaded file is too large.", "validation", 413),
    "AWS-0001": SystemErrorInfo("AWS-0001", "AWS authentication failed. Contact the system administrator.", "aws_authentication", 503),
    "AWS-0002": SystemErrorInfo("AWS-0002", "AWS access was denied. Check the configured IAM permissions.", "aws_authorization", 503),
    "AWS-0003": SystemErrorInfo("AWS-0003", "AWS storage is temporarily unreachable. Please try again.", "aws_connection", 503, True),
    "AWS-0004": SystemErrorInfo("AWS-0004", "The configured AWS storage bucket was not found.", "aws_configuration", 503),
    "AWS-0005": SystemErrorInfo("AWS-0005", "AWS storage has insufficient capacity for this operation.", "aws_storage", 507),
    "GCP-0001": SystemErrorInfo("GCP-0001", "Google Cloud authentication failed. Contact the system administrator.", "gcp_authentication", 503),
    "GCP-0002": SystemErrorInfo("GCP-0002", "Google Cloud storage or transcription service is unavailable.", "gcp_service", 503, True),
    "SRV-0001": SystemErrorInfo("SRV-0001", "Server storage is full. Contact the system administrator.", "server_storage", 507),
    "SRV-0002": SystemErrorInfo("SRV-0002", "The server cannot access the required file or directory.", "server_filesystem", 500),
    "SRV-0003": SystemErrorInfo("SRV-0003", "The server is processing the maximum number of audio jobs. Please try again shortly.", "server_capacity", 429, True),
}


def error_info(code: str) -> SystemErrorInfo:
    return CATALOG.get(code, CATALOG["SYS-0001"])


def classify_exception(exc: BaseException) -> SystemErrorInfo:
    """Map infrastructure exceptions to stable, non-sensitive public errors."""
    if isinstance(exc, OSError):
        if exc.errno in {errno.ENOSPC, getattr(errno, "EDQUOT", 122)}:
            return error_info("SRV-0001")
        if exc.errno in {errno.EACCES, errno.EPERM, errno.EROFS}:
            return error_info("SRV-0002")

    if isinstance(exc, (NoCredentialsError, PartialCredentialsError)):
        return error_info("AWS-0001")
    if isinstance(exc, (EndpointConnectionError, ConnectTimeoutError)):
        return error_info("AWS-0003")
    if isinstance(exc, ClientError):
        aws_code = str(exc.response.get("Error", {}).get("Code", ""))
        if aws_code in {"InvalidAccessKeyId", "SignatureDoesNotMatch", "ExpiredToken", "TokenRefreshRequired", "UnrecognizedClientException"}:
            return error_info("AWS-0001")
        if aws_code in {"AccessDenied", "AccessDeniedException", "AllAccessDisabled"}:
            return error_info("AWS-0002")
        if aws_code in {"NoSuchBucket", "NotFound", "404"}:
            return error_info("AWS-0004")
        if aws_code in {"InsufficientStorage", "StorageFull"}:
            return error_info("AWS-0005")
        if aws_code in {"RequestTimeout", "RequestTimeoutException", "SlowDown", "ServiceUnavailable", "InternalError"}:
            return error_info("AWS-0003")

    name = f"{exc.__class__.__module__}.{exc.__class__.__name__}".lower()
    text = str(exc).lower()
    if "google.auth" in name or "defaultcredentialserror" in name or "invalid_grant" in text:
        return error_info("GCP-0001")
    if "google" in name and any(term in text for term in ("unavailable", "deadline", "connection", "timeout")):
        return error_info("GCP-0002")
    if isinstance(exc, (TimeoutError, socket.timeout)) or "timeout" in name:
        return error_info("NET-0002")
    if isinstance(exc, (ConnectionError, socket.gaierror)) or any(term in name for term in ("connectionerror", "connecterror", "networkerror")):
        return error_info("NET-0001")
    return error_info("SYS-0001")


def error_payload(info: SystemErrorInfo, request_id: str, *, details: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {
        "code": info.code,
        "message": info.message,
        "category": info.category,
        "retryable": info.retryable,
        "requestId": request_id,
    }
    if details is not None:
        error["details"] = details
    return {"error": error, "detail": info.message}


def code_for_http_status(status: int) -> str:
    if status == 401:
        return "AUTH-0002"
    if status == 403:
        return "AUTH-0003"
    if status == 404:
        return "REQ-0002"
    if status == 413:
        return "REQ-0003"
    if status == 429:
        return "SRV-0003"
    if status == 422 or status == 400:
        return "REQ-0001"
    if status == 507:
        return "SRV-0001"
    if status == 502 or status == 503:
        return "NET-0001"
    if status == 504:
        return "NET-0002"
    return "SYS-0001"
