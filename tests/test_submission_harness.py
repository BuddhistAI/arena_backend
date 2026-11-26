"""Submission harness: a Submission is readable through the API, and the
submission worker scores queued Submissions only when a test drains it."""

import pytest

from test_create_challenge import category  # noqa: F401  (a fixture)
from test_model_submission import challenge, read, submit  # noqa: F401  (challenge is a fixture)


@pytest.fixture
def submitter(make_user):
    return make_user("user", name="submitter")


@pytest.fixture
def submission_id(client, headers_for, submitter, challenge):
    """A pending Submission by ``submitter``."""
    response = submit(client, headers_for(submitter), challenge, description="First try")
    assert response.status_code == 202, response.text
    return response.json()["id"]


def test_an_anonymous_visitor_reads_a_submission(client, submission_id):
    """Anyone, signed in or not, can read a Submission by its id."""
    response = client.get(f"/submissions/{submission_id}")

    assert response.status_code == 200, response.text
    assert response.json()["id"] == submission_id
    assert response.json()["description"] == "First try"
    assert response.json()["status"] == "pending"


def test_a_signed_in_user_lists_only_their_own_submissions(
    client, submission_id, submitter, user_headers, headers_for
):
    """``/submissions/my`` shows the signed-in user's Submissions and nobody else's."""
    mine = client.get("/submissions/my", headers=headers_for(submitter))
    someone_elses = client.get("/submissions/my", headers=user_headers)

    assert [s["id"] for s in mine.json()] == [submission_id]
    assert someone_elses.json() == []


def test_queued_work_waits_until_the_submission_worker_is_drained(client, submission_id, submission_queue):
    """No background thread picks up queued work during a test."""
    assert submission_queue.tasks.qsize() == 1
    assert read(client, submission_id)["status"] == "pending"


def test_draining_runs_queued_work_to_a_final_status(client, submission_id, submission_worker):
    """Draining scores every queued Submission on the calling thread until it
    is completed or failed."""
    assert submission_worker.drain() == 1

    assert read(client, submission_id)["status"] in ("completed", "failed")
