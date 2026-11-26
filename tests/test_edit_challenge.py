"""Edit Challenge: an admin changes a Challenge's presentation, data, Scorer or
parameters. Data and Scorer edits are re-validated like a create and are
refused once the Challenge is Locked (its first Submission arrived)."""

import datetime

import pytest

from test_create_challenge import admin_read, category, create_challenge, jsonl, zip_of  # noqa: F401


def edit_challenge(client, headers, challenge_id, data=None, files=None):
    return client.patch(f"/challenges/update/{challenge_id}", data=data or {}, files=files, headers=headers)


def jsonl_file(name, content):
    return (name, content, "application/jsonl")


@pytest.fixture
def challenge(client, admin_headers, category, db):  # noqa: F811
    """A CER Challenge with Inputs 1 and 2, whose repo is already synced."""
    response = create_challenge(client, admin_headers, category)
    assert response.status_code == 201, response.text
    created = response.json()
    set_columns(db, created["id"], sync_status="synced")
    return read(client, created)


def set_columns(db, challenge_id, **values):
    """Setup the API cannot do: the Sync Worker and RFC 2's first Submission
    set these."""
    from models import Challenge

    row = db.get(Challenge, challenge_id)
    for name, value in values.items():
        setattr(row, name, value)
    db.commit()


def lock(db, challenge):
    set_columns(db, challenge["id"], locked_at=datetime.datetime(2026, 11, 1, 12, 0))


read = admin_read


def test_signed_in_non_admin_is_not_allowed_to_edit(client, user_headers, challenge):
    response = edit_challenge(client, user_headers, challenge["id"], data={"title": "Renamed"})

    assert response.status_code == 403, response.text
    assert "admin" in response.json()["detail"].lower()
    assert read(client, challenge)["title"] == challenge["title"]


def test_before_the_lock_admin_replaces_the_ground_truth_and_sync_becomes_pending(
    client, admin_headers, challenge, storage
):
    new_ground_truth = jsonl({"id": "1", "reference": "ག"}, {"id": "2", "reference": "ང"})

    response = edit_challenge(
        client,
        admin_headers,
        challenge["id"],
        files={"ground_truth_file": jsonl_file("ground_truth.jsonl", new_ground_truth)},
    )

    assert response.status_code == 200, response.text
    assert response.json()["sync_status"] == "pending"
    assert read(client, challenge)["sync_status"] == "pending"
    [private_key] = [key for key in storage.objects if key.startswith("evalai/private/")]
    assert storage.objects[private_key] == new_ground_truth


def test_the_read_api_shows_whether_a_challenge_is_locked(client, db, challenge):
    assert challenge["locked"] is False

    lock(db, challenge)

    assert read(client, challenge)["locked"] is True
    [listed] = client.get("/challenges/list").json()
    assert listed["locked"] is True


NEW_INPUTS = jsonl({"id": "1", "input": "z"}, {"id": "2", "input": "w"})
NEW_GROUND_TRUTH = jsonl({"id": "1", "reference": "ག"}, {"id": "2", "reference": "ང"})

DATA_EDITS = [
    pytest.param({"data": {"scorer_name": "wer"}}, id="scorer"),
    pytest.param({"data": {"scorer_params": '{"language": "en"}'}}, id="parameters"),
    pytest.param(
        {"files": {"inputs_file": jsonl_file("inputs.jsonl", NEW_INPUTS)}},
        id="inputs",
    ),
    pytest.param(
        {"files": {"ground_truth_file": jsonl_file("ground_truth.jsonl", NEW_GROUND_TRUTH)}},
        id="ground-truth",
    ),
    pytest.param(
        {"files": {"media_archive_file": ("media.zip", zip_of("images/p1.png"), "application/zip")}},
        id="media-archive",
    ),
]


@pytest.mark.parametrize("edit", DATA_EDITS)
def test_after_the_lock_data_and_scorer_edits_are_refused(client, admin_headers, db, challenge, storage, edit):
    lock(db, challenge)
    before = read(client, challenge)
    stored_before = dict(storage.objects)

    response = edit_challenge(
        client,
        admin_headers,
        challenge["id"],
        data={"title": "Renamed", **edit.get("data", {})},
        files=edit.get("files"),
    )

    assert response.status_code == 409, response.text
    assert "Locked" in response.json()["detail"]
    assert read(client, challenge) == before  # not even the title changed
    assert storage.objects == stored_before


def test_after_the_lock_title_description_and_image_stay_editable(client, admin_headers, db, challenge):
    lock(db, challenge)

    response = edit_challenge(
        client,
        admin_headers,
        challenge["id"],
        data={"title": "Tibetan OCR, revised", "description": "Read the manuscript.", "image_uri": "https://img.test/new.png"},
    )

    assert response.status_code == 200, response.text
    edited = read(client, challenge)
    assert edited["title"] == "Tibetan OCR, revised"
    assert edited["description"] == "Read the manuscript."
    assert edited["image_uri"] == "https://img.test/new.png"
    assert edited["locked"] is True
    assert edited["sync_status"] == "pending"  # the repo shows the title and description
    assert edited["repo_name"] == challenge["repo_name"]  # renaming never renames the repo


def test_before_the_lock_a_ground_truth_that_does_not_match_the_stored_inputs_is_rejected(
    client, admin_headers, challenge, storage
):
    stored_before = dict(storage.objects)

    response = edit_challenge(
        client,
        admin_headers,
        challenge["id"],
        data={"title": "Renamed"},
        files={"ground_truth_file": jsonl_file("ground_truth.jsonl", jsonl({"id": "1", "reference": "ག"}))},
    )

    assert response.status_code == 422, response.text
    [error] = response.json()["errors"]
    assert (error["field"], error["id"]) == ("ground_truth_file", "2")
    assert read(client, challenge) == challenge
    assert storage.objects == stored_before


def test_before_the_lock_a_scorer_that_does_not_fit_the_stored_ground_truth_is_rejected(
    client, admin_headers, challenge
):
    # The stored References are strings; span_f1 needs lists of spans.
    response = edit_challenge(client, admin_headers, challenge["id"], data={"scorer_name": "span_f1"})

    assert response.status_code == 422, response.text
    assert {(e["field"], e["id"]) for e in response.json()["errors"]} == {
        ("ground_truth_file", "1"),
        ("ground_truth_file", "2"),
    }
    assert read(client, challenge) == challenge


def test_before_the_lock_an_invalid_parameter_is_rejected(client, admin_headers, challenge):
    response = edit_challenge(client, admin_headers, challenge["id"], data={"scorer_params": '{"lang": "en"}'})

    assert response.status_code == 422, response.text
    [error] = response.json()["errors"]
    assert error["field"] == "scorer_params"
    assert read(client, challenge) == challenge


def test_before_the_lock_admin_changes_the_parameters(client, admin_headers, challenge):
    response = edit_challenge(client, admin_headers, challenge["id"], data={"scorer_params": '{"language": "en"}'})

    assert response.status_code == 200, response.text
    edited = read(client, challenge)
    assert edited["scorer_params"] == {"language": "en"}
    assert edited["scorer_name"] == "cer"
    assert edited["sync_status"] == "pending"


def test_before_the_lock_admin_switches_scorer_with_new_data_and_gets_its_default_parameters(
    client, admin_headers, challenge, storage
):
    edit_challenge(client, admin_headers, challenge["id"], data={"scorer_params": '{"language": "en"}'})
    inputs = jsonl({"id": "a", "input": "om mani padme hum"}, {"id": "b", "input": "om ah hum"})
    ground_truth = jsonl(
        {"id": "a", "reference": [{"start": 0, "end": 1, "tag": "mantra"}]},
        {"id": "b", "reference": []},
    )

    response = edit_challenge(
        client,
        admin_headers,
        challenge["id"],
        data={"scorer_name": "span_f1"},
        files={
            "inputs_file": jsonl_file("inputs.jsonl", inputs),
            "ground_truth_file": jsonl_file("ground_truth.jsonl", ground_truth),
        },
    )

    assert response.status_code == 200, response.text
    edited = read(client, challenge)
    assert edited["scorer_name"] == "span_f1"
    assert edited["scorer_label"] == "Span IoU F1"
    assert edited["direction"] == "higher_is_better"
    assert edited["scorer_params"] == {"iou_threshold": 0.5}  # not the CER language
    assert edited["sync_status"] == "pending"
    assert storage.objects[edited["inputs_key"]] == inputs
    assert edited["inputs_key"] == challenge["inputs_key"]  # same public URL
    [private_key] = [key for key in storage.objects if key.startswith("evalai/private/")]
    assert storage.objects[private_key] == ground_truth


def test_before_the_lock_admin_adds_a_media_archive_with_inputs_that_point_into_it(
    client, admin_headers, challenge, storage
):
    inputs = jsonl(
        {"id": "1", "input": "page 1", "media": "images/p1.png"},
        {"id": "2", "input": "page 2", "media": "images/p2.png"},
    )
    archive = zip_of("images/p1.png", "images/p2.png")

    response = edit_challenge(
        client,
        admin_headers,
        challenge["id"],
        files={
            "inputs_file": jsonl_file("inputs.jsonl", inputs),
            "media_archive_file": ("media.zip", archive, "application/zip"),
        },
    )

    assert response.status_code == 200, response.text
    key = read(client, challenge)["media_archive_key"]
    assert key.startswith("evalai/public/")
    assert storage.objects[key] == archive


def test_before_the_lock_replaced_inputs_are_checked_against_the_stored_media_archive(
    client, admin_headers, challenge, storage
):
    edit_challenge(
        client,
        admin_headers,
        challenge["id"],
        files={
            "inputs_file": jsonl_file("inputs.jsonl", jsonl(
                {"id": "1", "input": "page 1", "media": "images/p1.png"},
                {"id": "2", "input": "page 2", "media": "images/p2.png"},
            )),
            "media_archive_file": ("media.zip", zip_of("images/p1.png", "images/p2.png"), "application/zip"),
        },
    )
    stored_before = dict(storage.objects)

    response = edit_challenge(
        client,
        admin_headers,
        challenge["id"],
        files={"inputs_file": jsonl_file("inputs.jsonl", jsonl(
            {"id": "1", "input": "page 1", "media": "images/p1.png"},
            {"id": "2", "input": "page 2", "media": "images/p3.png"},
        ))},
    )

    assert response.status_code == 422, response.text
    [error] = response.json()["errors"]
    assert (error["field"], error["id"]) == ("inputs_file", "2")
    assert "images/p3.png" in error["message"]
    assert storage.objects == stored_before


def test_a_title_used_by_another_challenge_is_rejected(client, admin_headers, category, challenge):  # noqa: F811
    other = create_challenge(client, admin_headers, category, title="Tibetan ASR")
    assert other.status_code == 201, other.text

    response = edit_challenge(client, admin_headers, challenge["id"], data={"title": "Tibetan ASR"})

    assert response.status_code == 409, response.text
    assert read(client, challenge)["title"] == challenge["title"]


def test_an_unknown_category_is_rejected(client, admin_headers, challenge):
    import uuid

    response = edit_challenge(client, admin_headers, challenge["id"], data={"category_id": str(uuid.uuid4())})

    assert response.status_code == 422, response.text
    [error] = response.json()["errors"]
    assert error["field"] == "category_id"
    assert read(client, challenge) == challenge


def test_editing_an_unknown_challenge_is_not_found(client, admin_headers):
    import uuid

    response = edit_challenge(client, admin_headers, uuid.uuid4(), data={"title": "Anything"})

    assert response.status_code == 404, response.text
