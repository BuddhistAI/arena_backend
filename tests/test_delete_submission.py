"""Deleting a Submission: the submitter or an admin deletes it, so it leaves
the lists and the leaderboard, its stored files are removed, and the next sync
removes its folder from the Challenge Repo. Deletion never frees a slot in the
daily limit and never undoes the Lock."""

import pytest

from test_create_challenge import admin_read, category  # noqa: F401  (category is a fixture)
from test_model_submission import (  # noqa: F401  (challenge, participant, span_challenge are fixtures)
    UNSCORABLE_SPANS,
    challenge,
    participant,
    span_challenge,
    submit,
)
from test_prompt_submission import submit_prompt
from test_skill_submission import submit_skill


def submitted(client, headers, challenge, submit_fn=submit, **fields):
    """Submit with ``submit_fn`` and return the new Submission's id."""
    response = submit_fn(client, headers, challenge, **fields)
    assert response.status_code == 202, response.text
    return response.json()["id"]


def delete(client, submission_id, headers):
    return client.delete(f"/submissions/{submission_id}", headers=headers)


def test_the_submitter_deletes_their_submission_and_it_is_gone(client, headers_for, participant, challenge):
    """The submitter gets 204, and reading the Submission then answers 404."""
    headers = headers_for(participant)
    submission_id = submitted(client, headers, challenge)

    response = delete(client, submission_id, headers)

    assert response.status_code == 204, response.text
    assert client.get(f"/submissions/{submission_id}").status_code == 404
    assert client.get(f"/submissions/{submission_id}/status").status_code == 404


def test_an_admin_deletes_anyones_submission(client, headers_for, admin_headers, participant, challenge):
    """An admin gets 204 on another user's Submission, which is then gone."""
    submission_id = submitted(client, headers_for(participant), challenge)

    response = delete(client, submission_id, admin_headers)

    assert response.status_code == 204, response.text
    assert client.get(f"/submissions/{submission_id}").status_code == 404


def test_another_user_cannot_delete_a_submission(client, headers_for, user_headers, participant, challenge):
    """A signed-in user who did not make the Submission gets 403 and it stays."""
    submission_id = submitted(client, headers_for(participant), challenge)

    response = delete(client, submission_id, user_headers)

    assert response.status_code == 403, response.text
    assert client.get(f"/submissions/{submission_id}").status_code == 200


def test_an_anonymous_visitor_cannot_delete_a_submission(client, headers_for, participant, challenge):
    """A request without sign-in is refused and the Submission stays."""
    submission_id = submitted(client, headers_for(participant), challenge)

    response = client.delete(f"/submissions/{submission_id}")

    assert response.status_code == 403, response.text
    assert client.get(f"/submissions/{submission_id}").status_code == 200


def test_deleting_an_already_deleted_submission_is_not_found(client, headers_for, participant, challenge):
    """A second delete of the same Submission answers 404."""
    headers = headers_for(participant)
    submission_id = submitted(client, headers, challenge)
    delete(client, submission_id, headers)

    assert delete(client, submission_id, headers).status_code == 404


def submission_objects(storage, submission_id):
    """The keys stored under the Submission's own storage folder."""
    return [key for key in storage.objects if f"/submissions/{submission_id}/" in key]


@pytest.mark.parametrize("submit_fn", [submit, submit_prompt, submit_skill])
def test_deleting_removes_the_artifact_and_predictions_from_storage(
    client, headers_for, participant, challenge, storage, submit_fn
):
    """The Predictions File and, for a Prompt or Skill, the Artifact are
    removed from object storage; the Challenge's own files stay."""
    headers = headers_for(participant)
    submission_id = submitted(client, headers, challenge, submit_fn)
    assert submission_objects(storage, submission_id) != []
    challenge_objects = [key for key in storage.objects if "/submissions/" not in key]

    delete(client, submission_id, headers)

    assert submission_objects(storage, submission_id) == []
    assert sorted(storage.objects) == sorted(challenge_objects)


REPO = "challenge-tibetan-ocr"


def submission_folders(repo_host):
    """The Submission folders in the Challenge Repo, e.g. ``models/x-1234abcd``."""
    return sorted(
        {
            "/".join(path.split("/")[:2])
            for path in repo_host.repos[REPO].files
            if path.startswith(("models/", "prompts/", "skills/")) and not path.endswith("/README.md")
        }
    )


def repo_path(client, submission_id):
    """The repo path of a published (``synced``) Submission."""
    response = client.get(f"/submissions/{submission_id}")
    assert response.status_code == 200, response.text
    assert response.json()["sync_status"] == "synced"
    return response.json()["repo_path"]


@pytest.fixture
def published(client, headers_for, participant, challenge, submission_worker, sync_worker, repo_host):
    """``published(submit_fn, **fields)`` submits as the participant, scores
    and publishes the Submission, and returns its id."""

    def _published(submit_fn=submit, **fields):
        submission_id = submitted(client, headers_for(participant), challenge, submit_fn, **fields)
        submission_worker.drain()
        sync_worker.drain()
        assert repo_path(client, submission_id) in submission_folders(repo_host)
        return submission_id

    return _published


def test_the_next_sync_removes_a_deleted_submissions_folder_in_a_new_commit(
    client, headers_for, participant, published, repo_host, sync_worker
):
    """A published Submission's folder is gone after the sync that its
    deletion queues, in a new commit; the other Submissions' folders stay."""
    deleted = published(name="Deleted model")
    kept = published(name="Kept model")
    kept_folder = repo_path(client, kept)
    commits_before = len(repo_host.repos[REPO].commits)

    delete(client, deleted, headers_for(participant))
    sync_worker.drain()

    assert len(repo_host.repos[REPO].commits) == commits_before + 1
    assert submission_folders(repo_host) == [kept_folder]
    assert repo_path(client, kept) == kept_folder


@pytest.mark.parametrize("submit_fn", [submit_prompt, submit_skill])
def test_a_deleted_prompt_or_skill_leaves_the_repo_without_a_reported_sync_failure(
    client, headers_for, participant, challenge, published, repo_host, sync_worker, caplog, submit_fn
):
    """Its Artifact is gone from storage, yet the sync removes its folder as
    a deletion, not as a lost Artifact: no sync failure is reported for it
    and the Challenge's sync succeeds."""
    submission_id = published(submit_fn)

    delete(client, submission_id, headers_for(participant))
    with caplog.at_level("WARNING", logger="sync_worker"):
        sync_worker.drain()

    assert submission_folders(repo_host) == []
    assert caplog.records == []
    assert admin_read(client, challenge)["sync_status"] == "synced"


def test_deleting_an_unscored_submission_writes_nothing_to_the_repo(
    client, headers_for, participant, challenge, repo_host, sync_worker
):
    """A Submission deleted before it was scored was never published, so no
    sync runs and the repo gets no commit."""
    sync_worker.drain()
    commits_before = len(repo_host.repos[REPO].commits)
    submission_id = submitted(client, headers_for(participant), challenge)

    delete(client, submission_id, headers_for(participant))
    sync_worker.drain()

    assert len(repo_host.repos[REPO].commits) == commits_before


def test_deleting_a_submission_that_failed_scoring_writes_nothing_to_the_repo(
    client, headers_for, participant, span_challenge, submission_worker, repo_host, sync_worker
):
    """A failed Submission is never published, so deleting it adds no commit."""
    sync_worker.drain()
    [repo] = repo_host.repos.values()
    commits_before = len(repo.commits)
    submission_id = submitted(client, headers_for(participant), span_challenge, predictions=UNSCORABLE_SPANS)
    submission_worker.drain()

    delete(client, submission_id, headers_for(participant))
    sync_worker.drain()

    assert len(repo.commits) == commits_before


def test_a_submission_deleted_while_queued_is_skipped_by_the_scoring_worker(
    client, headers_for, participant, challenge, submission_worker, repo_host, sync_worker, caplog
):
    """The worker passes over a Submission deleted before it was scored: no
    scoring error is logged and no sync is queued."""
    sync_worker.drain()
    commits_before = len(repo_host.repos[REPO].commits)
    submission_id = submitted(client, headers_for(participant), challenge)
    delete(client, submission_id, headers_for(participant))

    with caplog.at_level("ERROR", logger="submission_worker"):
        submission_worker.drain()
    sync_worker.drain()

    assert caplog.records == []
    assert len(repo_host.repos[REPO].commits) == commits_before


def test_a_scored_submission_deleted_before_its_first_sync_is_never_published(
    client, headers_for, participant, challenge, submission_worker, repo_host, sync_worker
):
    """Deleted after scoring but before the Sync Worker ran, it never gets a folder."""
    submission_id = submitted(client, headers_for(participant), challenge)
    submission_worker.drain()

    delete(client, submission_id, headers_for(participant))
    sync_worker.drain()

    assert submission_folders(repo_host) == []


def test_the_challenge_stays_locked_when_every_submission_is_deleted(
    client, headers_for, participant, challenge, published
):
    """The Lock is one way: deleting the only Submission leaves the Challenge Locked."""
    submission_id = published()

    delete(client, submission_id, headers_for(participant))

    assert client.get(f"/challenges/{challenge['id']}").json()["locked"] is True


def test_a_deleted_submission_cannot_be_resynced(client, headers_for, admin_headers, participant, published):
    """Resync answers 404 for a deleted Submission."""
    submission_id = published()
    delete(client, submission_id, headers_for(participant))

    response = client.post(f"/submissions/{submission_id}/resync", headers=admin_headers)

    assert response.status_code == 404, response.text


def test_a_deleted_submissions_preferred_model_is_no_longer_suggested(
    client, headers_for, participant, challenge
):
    """A Preferred Model id used only on a deleted Submission leaves the suggestions."""
    headers = headers_for(participant)
    deleted = submitted(client, headers, challenge, submit_prompt, preferred_model="only-on-deleted")
    submitted(client, headers, challenge, submit_prompt, preferred_model="gemini-2.5-pro")

    delete(client, deleted, headers)

    assert client.get("/submissions/preferred-models").json() == ["gemini-2.5-pro"]


@pytest.mark.parametrize("path", ["/results/challenge/{challenge_id}", "/results", "/results/simple"])
def test_a_deleted_submissions_results_leave_the_leaderboard_queries(
    client, headers_for, participant, challenge, path
):
    """Results recorded for a deleted Submission are no longer returned by the
    leaderboard's result queries; the kept Submission's still are."""
    headers = headers_for(participant)
    deleted = submitted(client, headers, challenge)
    kept = submitted(client, headers, challenge)
    for submission_id in (deleted, kept):
        response = client.post(
            "/results/create", json={"type": "CER", "submission_id": submission_id, "score": 0.1}, headers=headers
        )
        assert response.status_code == 201, response.text

    delete(client, deleted, headers)

    response = client.get(path.format(challenge_id=challenge["id"]))
    assert response.status_code == 200, response.text
    assert [r["submission_id"] for r in response.json()] == [kept]


def test_a_deleted_submission_leaves_the_challenge_debug_view(client, headers_for, participant, challenge):
    """The debug view of a Challenge lists only its Submissions that were not deleted."""
    headers = headers_for(participant)
    deleted = submitted(client, headers, challenge)
    kept = submitted(client, headers, challenge)

    delete(client, deleted, headers)

    body = client.get(f"/results/debug/challenge/{challenge['id']}").json()
    assert [s["id"] for s in body["submissions"]] == [kept]
    assert [s["submission_id"] for s in body["submission_results"]] == [kept]


def test_a_deleted_submission_leaves_the_debug_view_of_all_data(client, headers_for, participant, challenge):
    """The debug view of all data lists only Submissions that were not deleted."""
    headers = headers_for(participant)
    deleted = submitted(client, headers, challenge)
    kept = submitted(client, headers, challenge)

    delete(client, deleted, headers)

    assert [s["id"] for s in client.get("/results/debug/all-data").json()["submissions"]] == [kept]


@pytest.mark.parametrize("method", ["put", "patch"])
def test_a_submission_cannot_be_edited(client, headers_for, participant, challenge, method):
    """There is no endpoint to change a Submission after it is made."""
    headers = headers_for(participant)
    submission_id = submitted(client, headers, challenge)

    response = getattr(client, method)(f"/submissions/{submission_id}", json={"name": "Edited"}, headers=headers)

    assert response.status_code == 405, response.text
    assert client.get(f"/submissions/{submission_id}").json()["name"] == "Google Vision OCR"


@pytest.mark.parametrize("path", ["/submissions/my", "/submissions"])
def test_a_deleted_submission_leaves_the_submission_lists(
    client, headers_for, admin_headers, participant, challenge, path
):
    """The participant's list and the (admin's) list of all Submissions show
    the kept Submission and not the deleted one."""
    headers = headers_for(participant)
    deleted = submitted(client, headers, challenge)
    kept = submitted(client, headers, challenge)

    delete(client, deleted, headers)

    response = client.get(path, headers=admin_headers if path == "/submissions" else headers)
    assert response.status_code == 200, response.text
    assert [s["id"] for s in response.json()] == [kept]
