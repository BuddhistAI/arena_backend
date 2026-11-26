"""Object storage port: put, get, delete and public URL.

Public material (Inputs, Media Archive) goes under ``PUBLIC_PREFIX``, which
the bucket serves publicly. Ground Truth goes under ``PRIVATE_PREFIX`` and
never gets a public URL.

Routes take the port with ``Depends(get_object_storage)``; tests override
that dependency with ``InMemoryObjectStorage``.
"""

import io
import mimetypes
import os
from functools import lru_cache
from typing import BinaryIO, Dict, Optional, Protocol, Union

PUBLIC_PREFIX = "evalai/public/"
PRIVATE_PREFIX = "evalai/private/"

Body = Union[bytes, BinaryIO]


class ObjectNotFound(KeyError):
    """No object is stored under the key."""


class PrivateObjectError(ValueError):
    """A public URL was asked for an object outside the public prefix."""


def public_key(*parts: str) -> str:
    return PUBLIC_PREFIX + "/".join(parts)


def private_key(*parts: str) -> str:
    return PRIVATE_PREFIX + "/".join(parts)


def _require_public(key: str) -> None:
    if not key.startswith(PUBLIC_PREFIX):
        raise PrivateObjectError(f"{key!r} is not under the public prefix {PUBLIC_PREFIX!r}")


class ObjectStorage(Protocol):
    def put(self, key: str, body: Body, content_type: Optional[str] = None) -> None: ...

    def get(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None:
        """Remove the object; removing a missing object is not an error."""

    def public_url(self, key: str) -> str:
        """URL anyone can download a public object from. Raises
        ``PrivateObjectError`` for keys outside the public prefix."""


class InMemoryObjectStorage:
    """Fake for tests. ``objects`` maps each key to its bytes."""

    base_url = "https://storage.test"

    def __init__(self) -> None:
        self.objects: Dict[str, bytes] = {}
        self.content_types: Dict[str, Optional[str]] = {}

    def put(self, key: str, body: Body, content_type: Optional[str] = None) -> None:
        self.objects[key] = body if isinstance(body, bytes) else body.read()
        self.content_types[key] = content_type

    def get(self, key: str) -> bytes:
        try:
            return self.objects[key]
        except KeyError:
            raise ObjectNotFound(key) from None

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)
        self.content_types.pop(key, None)

    def public_url(self, key: str) -> str:
        _require_public(key)
        return f"{self.base_url}/{key}"


class S3ObjectStorage:
    """S3 implementation, configured from the same environment variables as
    the existing upload code (``AWS_ACCESS_KEY_ID``, ``AWS_SECRET_ACCESS_KEY``,
    ``AWS_REGION``, ``S3_BUCKET_NAME``, optional ``CLOUDFRONT_DOMAIN``).

    The bucket policy must grant public read on ``PUBLIC_PREFIX`` only; the
    policy in ``CRUD/s3_utils.py`` grants it on the whole bucket.
    """

    def __init__(self, bucket: str, client=None) -> None:
        if client is None:
            import boto3

            client = boto3.client(
                "s3",
                aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
                region_name=os.getenv("AWS_REGION"),
            )
        self.bucket = bucket
        self.client = client

    def put(self, key: str, body: Body, content_type: Optional[str] = None) -> None:
        fileobj = io.BytesIO(body) if isinstance(body, bytes) else body
        content_type = content_type or mimetypes.guess_type(key)[0] or "application/octet-stream"
        # upload_fileobj switches to multipart upload for large bodies.
        self.client.upload_fileobj(
            fileobj,
            self.bucket,
            key,
            ExtraArgs={"ContentType": content_type, "ContentDisposition": "inline"},
        )

    def get(self, key: str) -> bytes:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
        except self.client.exceptions.NoSuchKey:
            raise ObjectNotFound(key) from None
        return response["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def public_url(self, key: str) -> str:
        from CRUD.s3_utils import generate_public_s3_url

        _require_public(key)
        return generate_public_s3_url(key)


@lru_cache()
def _s3_object_storage() -> S3ObjectStorage:
    bucket = os.getenv("S3_BUCKET_NAME")
    if not bucket:
        raise RuntimeError("S3_BUCKET_NAME is not set")
    return S3ObjectStorage(bucket)


def get_object_storage() -> ObjectStorage:
    """FastAPI dependency for the object storage port."""
    return _s3_object_storage()
