"""Submission harness: a stored Submission is readable through the API, and
the submission worker runs queued work only when a test drains it.

Creating a Submission through the API comes with RFC 2's intake, so these
tests store the Submission row directly as setup."""

import pytest

from test_create_challenge import category, create_challenge  # noqa: F401  (category is a fixture)


@pytest.fixture
def submitter(make_user):
    return make_user("user", name="submitter")


@pytest.fixture
def stored_submission(client, admin_headers, category, submitter, db):
    """A pending Submission by ``submitter`` on a freshly created Challenge."""
    from models import Model, Submission

    response = create_challenge(client, admin_headers, category)
    assert response.status_code == 201, response.text
    model = Model(name="Google Vision OCR", created_by=submitter.id, updated_by=submitter.id)
    db.add(model)
    db.commit()
    submission = Submission(
        user_id=submitter.id,
        model_id=model.id,
        challenge_id=response.json()["id"],
        description="First try",
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)
    return submission


def test_an_anonymous_visitor_reads_a_submission(client, stored_submission):
    """Anyone, signed in or not, can read a Submission by its id."""
    response = client.get(f"/submissions/{stored_submission.id}")

    assert response.status_code == 200, response.text
    assert response.json()["id"] == str(stored_submission.id)
    assert response.json()["description"] == "First try"
    assert response.json()["status"] == "pending"


def test_a_signed_in_user_lists_only_their_own_submissions(
    client, stored_submission, submitter, user_headers, headers_for
):
    """``/submissions/my`` shows the signed-in user's Submissions and nobody else's."""
    mine = client.get("/submissions/my", headers=headers_for(submitter))
    someone_elses = client.get("/submissions/my", headers=user_headers)

    assert [s["id"] for s in mine.json()] == [str(stored_submission.id)]
    assert someone_elses.json() == []


PREDICTIONS = b'[{"filename": "1", "prediction": "\\u0f40"}]'


def queue_predictions(submission_queue, submission):
    from submission_worker import SubmissionTask

    submission_queue.add_task(
        SubmissionTask(
            submission_id=str(submission.id), file_content=PREDICTIONS, filename="predictions.json"
        )
    )


def read_status(client, submission):
    response = client.get(f"/submissions/{submission.id}")
    assert response.status_code == 200, response.text
    return response.json()["status"]


def test_queued_work_waits_until_the_submission_worker_is_drained(
    client, stored_submission, submission_queue, storage
):
    """No background thread picks up queued work during a test."""
    stored_before = dict(storage.objects)
    queue_predictions(submission_queue, stored_submission)

    assert read_status(client, stored_submission) == "pending"
    assert storage.objects == stored_before


def test_draining_runs_queued_work_to_a_final_status(
    client, stored_submission, submission_queue, submission_worker
):
    """Draining processes every queued Submission on the calling thread
    until it is completed or failed."""
    queue_predictions(submission_queue, stored_submission)

    submission_worker.drain()

    assert read_status(client, stored_submission) in ("completed", "failed")


def test_the_worker_stores_predictions_privately_through_object_storage(
    client, stored_submission, submission_queue, submission_worker, storage
):
    """The predictions file goes to the object storage port, under the
    private prefix, never to S3 directly."""
    from ports.object_storage import PRIVATE_PREFIX

    stored_before = set(storage.objects)
    queue_predictions(submission_queue, stored_submission)
    submission_worker.drain()

    [key] = set(storage.objects) - stored_before
    body = storage.objects[key]
    assert key.startswith(PRIVATE_PREFIX)
    assert str(stored_submission.id) in key
    assert body == PREDICTIONS
