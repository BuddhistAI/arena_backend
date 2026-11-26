"""The Challenge's Scorer, storage and sync fields as seen through the read API."""

import datetime

import pytest


@pytest.fixture
def scored_challenge(db, make_user):
    """A Challenge saved with every new field set, as ticket 04's create
    endpoint will save it. Inserted directly because creation is not rebuilt yet."""
    from models import Category, Challenge

    admin = make_user("admin")
    category = Category(name="OCR", created_by=admin.id, updated_by=admin.id)
    db.add(category)
    db.flush()
    challenge = Challenge(
        title="Tibetan OCR",
        description="Read the woodblock.",
        image_uri="https://img.test/ocr.png",
        category_id=category.id,
        created_by=admin.id,
        status="active",
        scorer_name="cer",
        scorer_params={"language": "bo"},
        direction="lower_is_better",
        scorer_label="Character Error Rate",
        package_version="0.2.0",
        inputs_key="evalai/public/challenges/tibetan-ocr/inputs.jsonl",
        media_archive_key="evalai/public/challenges/tibetan-ocr/media.zip",
        ground_truth_key="evalai/private/challenges/tibetan-ocr/ground_truth.jsonl",
        repo_name="challenge-tibetan-ocr",
        sync_status="failed",
        sync_attempts=5,
        last_sync_error="GitHub returned 502",
        locked_at=datetime.datetime(2026, 11, 20, 8, 30),
    )
    db.add(challenge)
    db.commit()
    db.refresh(challenge)
    return challenge


EXPECTED_PUBLIC_FIELDS = {
    "title": "Tibetan OCR",
    "description": "Read the woodblock.",
    "image_uri": "https://img.test/ocr.png",
    "status": "active",
    "scorer_name": "cer",
    "scorer_params": {"language": "bo"},
    "direction": "lower_is_better",
    "scorer_label": "Character Error Rate",
    "package_version": "0.2.0",
    "repo_name": "challenge-tibetan-ocr",
    "sync_status": "failed",
    "sync_attempts": 5,
    "locked_at": "2026-11-20T08:30:00",
}


def _assert_public_view(body):
    for field, expected in EXPECTED_PUBLIC_FIELDS.items():
        assert body[field] == expected, field
    # Storage keys and sync errors are internals: participants get the public
    # links from challenge.json. The old Ground Truth URL field is gone too.
    for internal in ("inputs_key", "media_archive_key", "ground_truth_key", "last_sync_error", "ground_truth"):
        assert internal not in body, internal


def test_reading_a_challenge_returns_its_scorer_and_sync_fields_but_no_internals(client, scored_challenge):
    response = client.get(f"/challenges/{scored_challenge.id}")

    assert response.status_code == 200, response.text
    _assert_public_view(response.json())


def test_listing_challenges_returns_their_scorer_and_sync_fields_but_no_internals(client, scored_challenge):
    response = client.get("/challenges/list")

    assert response.status_code == 200, response.text
    [listed] = response.json()
    _assert_public_view(listed)
    assert listed["category"]["name"] == "OCR"


INTERNAL_FIELDS = {
    "inputs_key": "evalai/public/challenges/tibetan-ocr/inputs.jsonl",
    "media_archive_key": "evalai/public/challenges/tibetan-ocr/media.zip",
    "ground_truth_key": "evalai/private/challenges/tibetan-ocr/ground_truth.jsonl",
    "last_sync_error": "GitHub returned 502",
}


def test_admins_read_a_challenges_storage_keys_and_last_sync_error(client, scored_challenge, admin_headers):
    response = client.get(f"/challenges/admin/{scored_challenge.id}", headers=admin_headers)

    assert response.status_code == 200, response.text
    body = response.json()
    for field, expected in {**EXPECTED_PUBLIC_FIELDS, **INTERNAL_FIELDS}.items():
        assert body[field] == expected, field
