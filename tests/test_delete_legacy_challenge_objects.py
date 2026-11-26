"""The one-off script that deletes the legacy challenges' S3 objects."""

import pytest

from scripts.delete_legacy_challenge_objects import main

LEGACY = [
    "evalai/ground_truth/Tibetan_OCR_1b2c/ground_truth.json",
    "evalai/challenges/Tibetan OCR/5f6a_predictions.json",
]
KEPT = [
    "evalai/public/challenges/tibetan-ocr/inputs.jsonl",
    "evalai/private/challenges/tibetan-ocr/ground_truth.jsonl",
    "evalai/uploads/logo.png",
    "evalai/test_uploads/abc.json",
    "arena/translations/out.json",
]


class InMemoryBucket:
    def __init__(self, keys):
        self.keys = set(keys)

    def list_keys(self, prefix):
        return sorted(k for k in self.keys if k.startswith(prefix))

    def delete_keys(self, keys):
        self.keys -= set(keys)


@pytest.fixture
def bucket():
    return InMemoryBucket(LEGACY + KEPT)


def test_dry_run_lists_the_legacy_objects_and_deletes_nothing(bucket, capsys):
    exit_code = main([], bucket=bucket)

    assert exit_code == 0
    out = capsys.readouterr().out
    for key in LEGACY:
        assert key in out
    for key in KEPT:
        assert key not in out
    assert "dry run" in out.lower()
    assert bucket.keys == set(LEGACY + KEPT)


def test_execute_deletes_only_the_legacy_objects(bucket, capsys):
    exit_code = main(["--execute"], bucket=bucket)

    assert exit_code == 0
    assert bucket.keys == set(KEPT)
    assert "deleted 2" in capsys.readouterr().out.lower()
