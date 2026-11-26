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
    "inputs_key": "evalai/public/challenges/tibetan-ocr/inputs.jsonl",
    "media_archive_key": "evalai/public/challenges/tibetan-ocr/media.zip",
    "repo_name": "challenge-tibetan-ocr",
    "sync_status": "failed",
    "sync_attempts": 5,
    "last_sync_error": "GitHub returned 502",
    "locked_at": "2026-11-20T08:30:00",
}


def _assert_public_view(body):
    for field, expected in EXPECTED_PUBLIC_FIELDS.items():
        assert body[field] == expected, field
    # The Ground Truth is private: neither its storage key nor the old URL
    # field is part of the public read API.
    assert "ground_truth_key" not in body
    assert "ground_truth" not in body


def test_reading_a_challenge_returns_its_scorer_storage_and_sync_fields(client, scored_challenge):
    response = client.get(f"/challenges/{scored_challenge.id}")

    assert response.status_code == 200, response.text
    _assert_public_view(response.json())


def test_listing_challenges_returns_their_scorer_storage_and_sync_fields(client, scored_challenge):
    response = client.get("/challenges/list")

    assert response.status_code == 200, response.text
    [listed] = response.json()
    _assert_public_view(listed)
    assert listed["category"]["name"] == "OCR"
