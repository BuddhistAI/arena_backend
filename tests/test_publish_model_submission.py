"""Publishing Model Submissions: once a Submission is scored, the Sync Worker
writes its metadata to the Challenge Repo as
``models/<slug>-<id>/submission.json``, retrying when GitHub is down and
recording the outcome on the Submission."""

import json

import pytest

from test_create_challenge import category, jsonl  # noqa: F401  (category is a fixture)
from test_model_submission import UNSCORABLE_SPANS, challenge, read, span_challenge, submit  # noqa: F401

REPO = "challenge-tibetan-ocr"


@pytest.fixture
def participant(make_user):
    return make_user("user", name="tenzin")


@pytest.fixture
def scored(client, headers_for, participant, challenge, submission_worker):
    """``scored(**fields)`` submits a Model Submission as the participant,
    scores it and returns its id."""

    def _scored(**fields):
        response = submit(client, headers_for(participant), challenge, **fields)
        assert response.status_code == 202, response.text
        submission_worker.drain()
        return response.json()["id"]

    return _scored


def submission_folders(repo_host, repo=REPO):
    """The Submission folders under ``models/`` in the repo."""
    return sorted(
        path.rsplit("/", 1)[0] for path in repo_host.repos[repo].files
        if path.startswith("models/") and path != "models/README.md"
    )


def test_a_scored_model_submission_is_published_as_its_submission_json(
    client, challenge, scored, repo_host, sync_worker
):
    """A completed Model Submission gets ``models/<slug>-<id>/submission.json``
    holding its metadata, its Public Summary beside its Headline Metric, and
    no other file."""
    submission_id = scored(name="Google Vision OCR", model_link="https://cloud.google.com/vision")

    sync_worker.drain()

    folder = f"models/google-vision-ocr-{submission_id[:8]}"
    files = repo_host.repos[REPO].files
    assert [path for path in files if path.startswith(folder)] == [f"{folder}/submission.json"]
    assert json.loads(files[f"{folder}/submission.json"]) == {
        "schema_version": 1,
        "submission_id": submission_id,
        "challenge_id": challenge["id"],
        "type": "model",
        "name": "Google Vision OCR",
        "description": "Vision API, default settings.",
        "submitter": "tenzin",
        "scorer_name": "cer",
        "headline_metric": 0.0,
        "public_summary": [
            {"key": "corpus", "label": "Corpus CER", "value": 0.0, "format": {"style": "percent", "decimals": 2}}
        ],
        "preferred_model": None,
        "model_link": "https://cloud.google.com/vision",
        "license": None,
        "created_at": "2026-11-26T12:00:00Z",
    }


def test_a_submission_scored_after_the_repo_was_synced_is_published(
    client, scored, repo_host, sync_worker
):
    """Scoring a Submission queues a sync of its Challenge, so it is published
    even when the Challenge Repo was already up to date."""
    sync_worker.drain()
    submission_id = scored(name="Google Vision OCR")

    sync_worker.drain()

    assert submission_folders(repo_host) == [f"models/google-vision-ocr-{submission_id[:8]}"]


def test_a_published_submission_reads_back_synced_with_its_repo_path(client, scored, sync_worker):
    """Reading a Submission returns sync status ``pending`` until it is
    published, then ``synced`` with the folder's path in the repo."""
    submission_id = scored(name="Google Vision OCR")
    pending = read(client, submission_id)
    assert (pending["sync_status"], pending["repo_path"]) == ("pending", None)

    sync_worker.drain()

    synced = read(client, submission_id)
    assert (synced["sync_status"], synced["repo_path"]) == (
        "synced", f"models/google-vision-ocr-{submission_id[:8]}"
    )


def test_two_submissions_with_the_same_name_land_in_different_folders(scored, repo_host, sync_worker):
    """Each folder ends with its Submission's short id, so equal names never
    overwrite each other."""
    first = scored(name="Google Vision OCR")
    second = scored(name="Google Vision OCR")

    sync_worker.drain()

    assert submission_folders(repo_host) == sorted([
        f"models/google-vision-ocr-{first[:8]}", f"models/google-vision-ocr-{second[:8]}"
    ])


def test_a_tibetan_name_is_slugged_from_its_wylie(scored, repo_host, sync_worker):
    """A Tibetan name is transliterated to Wylie before slugging."""
    submission_id = scored(name="བཀྲ་ཤིས་ OCR")

    sync_worker.drain()

    assert submission_folders(repo_host) == [f"models/bkra-shis-ocr-{submission_id[:8]}"]


def test_a_name_that_gives_no_slug_uses_the_short_id_alone(scored, repo_host, sync_worker):
    """A name with nothing to slug is published under its short id."""
    submission_id = scored(name="中文 !!")

    sync_worker.drain()

    assert submission_folders(repo_host) == [f"models/{submission_id[:8]}"]


def test_a_long_name_gives_a_shortened_slug(scored, repo_host, sync_worker):
    """The slug keeps at most 60 characters of the name."""
    submission_id = scored(name="ocr " * 40)

    sync_worker.drain()

    [folder] = submission_folders(repo_host)
    assert folder == f"models/{'-'.join(['ocr'] * 15)}-{submission_id[:8]}"


def test_the_published_metadata_never_holds_the_submitters_email_or_user_id(
    participant, scored, repo_host, sync_worker
):
    """Only the public username identifies the submitter in the repo."""
    scored()

    sync_worker.drain()

    published = b"".join(repo_host.repos[REPO].files.values()).decode()
    assert participant.username in published
    assert participant.email not in published
    assert participant.id not in published


def test_predictions_are_never_written_to_the_repo(scored, repo_host, sync_worker):
    """No file in the repo holds the Predictions File or a prediction."""
    scored(predictions=jsonl({"id": "1", "prediction": "PRED-ONE"}, {"id": "2", "prediction": "PRED-TWO"}))

    sync_worker.drain()

    files = repo_host.repos[REPO].files
    assert not any("prediction" in path.lower() and path.startswith("models/") for path in files)
    published = b"".join(files.values()).decode()
    assert "PRED-ONE" not in published
    assert "PRED-TWO" not in published


def test_a_submission_that_failed_scoring_is_never_published(
    client, headers_for, participant, span_challenge, submission_worker, repo_host, sync_worker
):
    """A ``failed`` Submission gets no folder and no repo path."""
    response = submit(client, headers_for(participant), span_challenge, predictions=UNSCORABLE_SPANS)
    submission_worker.drain()

    sync_worker.drain()

    repo = next(iter(repo_host.repos))
    assert submission_folders(repo_host, repo) == []
    assert read(client, response.json()["id"], headers_for(participant))["repo_path"] is None


def test_a_later_challenge_sync_keeps_the_published_submissions(
    client, admin_headers, challenge, scored, repo_host, sync_worker
):
    """Every sync rebuilds the whole tree, so editing the Challenge afterwards
    leaves the Submission folders in place."""
    submission_id = scored(name="Google Vision OCR")
    sync_worker.drain()

    edit = client.patch(
        f"/challenges/update/{challenge['id']}", data={"description": "New words."}, headers=admin_headers
    )
    assert edit.status_code == 200, edit.text
    sync_worker.drain()

    assert len(repo_host.repos[REPO].commits) == 2
    assert submission_folders(repo_host) == [f"models/google-vision-ocr-{submission_id[:8]}"]


def test_when_github_is_down_the_submission_stays_scored_and_its_sync_fails_after_5_attempts(
    client, scored, repo_host, sync_worker
):
    """A repo host outage never touches the score: the sync stays ``pending``
    while attempts remain, then is ``failed`` after the fifth."""
    sync_worker.drain()
    submission_id = scored()
    repo_host.fail_next(5, "GitHub is down")

    assert sync_worker.run_once() == 1
    retrying = read(client, submission_id)
    assert (retrying["status"], retrying["sync_status"]) == ("completed", "pending")

    sync_worker.drain()

    failed = read(client, submission_id)
    assert (failed["status"], failed["headline_metric"]) == ("completed", 0.0)
    assert (failed["sync_status"], failed["repo_path"]) == ("failed", None)
    assert submission_folders(repo_host) == []


@pytest.fixture
def failed_sync(scored, repo_host, sync_worker):
    """The id of a scored Submission whose sync failed after 5 attempts."""
    sync_worker.drain()
    submission_id = scored(name="Google Vision OCR")
    repo_host.fail_next(5, "GitHub is down")
    sync_worker.drain()
    return submission_id


def resync(client, submission_id, headers):
    return client.post(f"/submissions/{submission_id}/resync", headers=headers)


def test_an_admin_resync_publishes_a_submission_whose_sync_failed(
    client, admin_headers, failed_sync, repo_host, sync_worker
):
    """Resync puts the sync back to ``pending``; the next sync rebuilds the
    folder from the arena's data and the Submission is ``synced``."""
    response = resync(client, failed_sync, admin_headers)
    assert response.status_code == 200, response.text
    assert response.json()["sync_status"] == "pending"

    sync_worker.drain()

    folder = f"models/google-vision-ocr-{failed_sync[:8]}"
    assert submission_folders(repo_host) == [folder]
    assert (read(client, failed_sync)["sync_status"], read(client, failed_sync)["repo_path"]) == ("synced", folder)


def test_a_non_admin_cannot_resync_a_submission(
    client, headers_for, participant, failed_sync, repo_host, sync_worker
):
    """Only admins may Resync, even the submitter is refused."""
    response = resync(client, failed_sync, headers_for(participant))
    assert response.status_code == 403, response.text

    sync_worker.drain()

    assert read(client, failed_sync)["sync_status"] == "failed"
    assert submission_folders(repo_host) == []


def test_a_submission_that_failed_scoring_cannot_be_resynced(
    client, admin_headers, headers_for, participant, span_challenge, submission_worker
):
    """Only scored Submissions are published, so Resync refuses others."""
    response = submit(client, headers_for(participant), span_challenge, predictions=UNSCORABLE_SPANS)
    submission_worker.drain()

    refused = resync(client, response.json()["id"], admin_headers)

    assert refused.status_code == 409, refused.text
    assert "scored" in refused.json()["detail"]


def test_a_later_submission_whose_folder_would_collide_uses_its_full_id(
    client, clock, scored, repo_host, sync_worker, monkeypatch
):
    """Two Submissions whose folders would share a name never overwrite each
    other: the later one's folder ends in its full id instead."""
    import datetime
    import uuid

    # Same first 8 characters; the earlier one sorts after the later by id.
    earlier = uuid.UUID("aaaaaaaa-ffff-4000-8000-000000000000")
    later = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000000")
    ids = iter([earlier, later])
    monkeypatch.setattr("service.submission_intake.new_submission_id", lambda: next(ids))
    scored(name="Google Vision OCR")
    clock.advance(datetime.timedelta(minutes=1))
    scored(name="Google Vision OCR")

    sync_worker.drain()

    assert submission_folders(repo_host) == [
        "models/google-vision-ocr-aaaaaaaa",
        f"models/google-vision-ocr-{later}",
    ]
