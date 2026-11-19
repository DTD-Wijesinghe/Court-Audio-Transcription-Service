from __future__ import annotations

import mimetypes
from functools import lru_cache
from typing import Iterator, Optional

import boto3
from botocore.exceptions import ClientError


def _require_bucket(bucket: str) -> str:
    bucket_name = (bucket or "").strip()
    if not bucket_name:
        raise ValueError("S3_BUCKET_NAME is not configured.")
    return bucket_name


def _object_key(key: str) -> str:
    return key_from_storage_uri(key).strip().lstrip("/")


def _storage_uri(bucket: str, key: str) -> str:
    return f"s3://{_require_bucket(bucket)}/{_object_key(key)}"


@lru_cache(maxsize=8)
def _s3_client(region: str | None = None):
    return boto3.client("s3", region_name=region or None)


def key_from_storage_uri(uri_or_key: str) -> str:
    value = (uri_or_key or "").strip()
    if value.startswith("s3://"):
        remainder = value[len("s3://"):]
        return remainder.split("/", 1)[1] if "/" in remainder else ""
    return value.lstrip("/")


def upload_file_to_s3(bucket: str, key: str, local_path: str, region: str | None = None) -> str:
    bucket_name = _require_bucket(bucket)
    object_key = _object_key(key)
    content_type = mimetypes.guess_type(local_path)[0] or "application/octet-stream"
    _s3_client(region).upload_file(
        local_path,
        bucket_name,
        object_key,
        ExtraArgs={"ContentType": content_type},
    )
    return _storage_uri(bucket_name, object_key)


def upload_text(
    bucket: str,
    key: str,
    text: str,
    content_type: str = "text/plain; charset=utf-8",
    region: str | None = None,
) -> str:
    return upload_bytes(
        bucket,
        key,
        (text or "").encode("utf-8"),
        content_type=content_type,
        region=region,
    )


def upload_bytes(
    bucket: str,
    key: str,
    data: bytes,
    content_type: str = "application/octet-stream",
    region: str | None = None,
) -> str:
    bucket_name = _require_bucket(bucket)
    object_key = _object_key(key)
    _s3_client(region).put_object(
        Bucket=bucket_name,
        Key=object_key,
        Body=data,
        ContentType=content_type,
    )
    return _storage_uri(bucket_name, object_key)


def _is_missing_object(exc: ClientError) -> bool:
    error = exc.response.get("Error", {})
    status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return status == 404 or error.get("Code") in {"404", "NoSuchKey", "NoSuchBucket", "NotFound"}


def get_s3_object_bytes(bucket: str, key: str, region: str | None = None) -> Optional[bytes]:
    bucket_name = _require_bucket(bucket)
    object_key = _object_key(key)
    try:
        response = _s3_client(region).get_object(Bucket=bucket_name, Key=object_key)
        body = response["Body"]
        try:
            return body.read()
        finally:
            body.close()
    except ClientError as exc:
        if _is_missing_object(exc):
            return None
        raise


def get_s3_object_text(bucket: str, key: str, region: str | None = None) -> Optional[str]:
    data = get_s3_object_bytes(bucket, key, region=region)
    if data is None:
        return None
    return data.decode("utf-8", errors="replace")


def list_s3_prefix_objects(bucket: str, prefix: str, region: str | None = None) -> list[dict[str, object]]:
    bucket_name = _require_bucket(bucket)
    clean_prefix = (prefix or "").lstrip("/")
    objects: list[dict[str, object]] = []
    paginator = _s3_client(region).get_paginator("list_objects_v2")

    for page in paginator.paginate(Bucket=bucket_name, Prefix=clean_prefix):
        for item in page.get("Contents", []):
            objects.append(dict(item))
    return objects


def list_s3_directory_entries(bucket: str, prefix: str, region: str | None = None) -> list[dict[str, object]]:
    bucket_name = _require_bucket(bucket)
    clean_prefix = (prefix or "").strip().lstrip("/")
    if clean_prefix and not clean_prefix.endswith("/"):
        clean_prefix = f"{clean_prefix}/"

    entries: list[dict[str, object]] = []
    paginator = _s3_client(region).get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket_name, Prefix=clean_prefix, Delimiter="/"):
        for item in page.get("Contents", []):
            key = str(item.get("Key") or "")
            if key == clean_prefix:
                continue
            entry = dict(item)
            entry["Type"] = "object"
            entries.append(entry)
        for item in page.get("CommonPrefixes", []):
            key = str(item.get("Prefix") or "").rstrip("/")
            if key:
                entries.append({"Key": key, "Size": 0, "LastModified": "", "Type": "directory"})
    return entries


def presign_s3_url(bucket: str, key: str, expires: int = 900, region: str | None = None) -> Optional[str]:
    bucket_name = _require_bucket(bucket)
    object_key = _object_key(key)
    return _s3_client(region).generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket_name, "Key": object_key},
        ExpiresIn=expires,
    )


def storage_record(name: str, key: str, size: int, updated_at: str) -> dict[str, object]:
    return {"name": name, "s3Key": key, "size": size, "updatedAt": updated_at}


def _stream_body(body, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
    try:
        for chunk in body.iter_chunks(chunk_size=chunk_size):
            if chunk:
                yield chunk
    finally:
        body.close()


def stream_s3_object_range(
    bucket: str,
    key: str,
    range_header: str | None = None,
    region: str | None = None,
):
    """Stream an S3 object in chunks, forwarding Range for seeking support."""
    bucket_name = _require_bucket(bucket)
    object_key = _object_key(key)
    params = {"Bucket": bucket_name, "Key": object_key}
    if range_header:
        params["Range"] = range_header

    try:
        response = _s3_client(region).get_object(**params)
    except ClientError as exc:
        if _is_missing_object(exc):
            return None, None, None
        raise

    headers: dict[str, str] = {}
    if response.get("ContentRange"):
        headers["Content-Range"] = str(response["ContentRange"])
    if response.get("ContentLength") is not None:
        headers["Content-Length"] = str(response["ContentLength"])
    if response.get("AcceptRanges"):
        headers["Accept-Ranges"] = str(response["AcceptRanges"])

    status_code = int(response.get("ResponseMetadata", {}).get("HTTPStatusCode") or (206 if range_header else 200))
    return _stream_body(response["Body"]), status_code, headers
