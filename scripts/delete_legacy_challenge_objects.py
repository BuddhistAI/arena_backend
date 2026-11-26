"""Delete the S3 objects of the legacy (EvalAI-shaped) challenges.

One-off companion to Alembic revision a3c1e7d2f9b4, which deletes the legacy
Challenge, Submission and Result rows. The objects are found by prefix, not
by the deleted rows, so this can run before or after the migration:

- ``evalai/ground_truth/``  legacy ground-truth JSON files
- ``evalai/challenges/``    legacy submission JSON files

New Challenges store under ``evalai/public/`` and ``evalai/private/``, which
this never touches; neither does it touch ``evalai/uploads/`` or anything
else in the bucket.

Dry run by default; pass ``--execute`` to delete. Run from the arena_backend
root, with the AWS variables and ``S3_BUCKET_NAME`` set (``.env`` is read):

    python -m scripts.delete_legacy_challenge_objects            # list
    python -m scripts.delete_legacy_challenge_objects --execute  # delete
"""

import argparse
import os
import sys
from typing import Iterable, List, Optional, Protocol

LEGACY_PREFIXES = ("evalai/ground_truth/", "evalai/challenges/")


class Bucket(Protocol):
    def list_keys(self, prefix: str) -> Iterable[str]: ...

    def delete_keys(self, keys: List[str]) -> None: ...


class S3Bucket:
    _BATCH = 1000  # S3 DeleteObjects limit

    def __init__(self, name: str, client=None) -> None:
        if client is None:
            import boto3

            client = boto3.client(
                "s3",
                aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
                region_name=os.getenv("AWS_REGION"),
            )
        self.name = name
        self.client = client

    def list_keys(self, prefix: str) -> Iterable[str]:
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.name, Prefix=prefix):
            for obj in page.get("Contents", []):
                yield obj["Key"]

    def delete_keys(self, keys: List[str]) -> None:
        for start in range(0, len(keys), self._BATCH):
            batch = keys[start:start + self._BATCH]
            response = self.client.delete_objects(
                Bucket=self.name,
                Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True},
            )
            errors = response.get("Errors", [])
            if errors:
                raise RuntimeError(f"S3 refused to delete {len(errors)} object(s): {errors[:5]}")


def _s3_bucket_from_env() -> S3Bucket:
    from dotenv import load_dotenv

    load_dotenv()
    name = os.getenv("S3_BUCKET_NAME")
    if not name:
        raise SystemExit("S3_BUCKET_NAME is not set")
    return S3Bucket(name)


def main(argv: Optional[List[str]] = None, bucket: Optional[Bucket] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--execute", action="store_true", help="delete the objects (default: dry run, list only)"
    )
    args = parser.parse_args(argv)
    if bucket is None:
        bucket = _s3_bucket_from_env()

    keys = [key for prefix in LEGACY_PREFIXES for key in bucket.list_keys(prefix)]
    for key in keys:
        print(key)

    if not args.execute:
        print(f"Dry run: {len(keys)} legacy object(s) would be deleted. Pass --execute to delete.")
        return 0

    bucket.delete_keys(keys)
    print(f"Deleted {len(keys)} legacy object(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
