"""Daily Submission limit: a signed-in participant stores at most 10
Submissions per Challenge per UTC day; the next is refused with 429 and the
time the limit resets (the next 00:00 UTC). Admins are never limited."""

import datetime

from test_create_challenge import category, create_challenge, jsonl  # noqa: F401  (category is a fixture)
from test_model_submission import (  # noqa: F401  (challenge, participant, span_challenge are fixtures)
    UNSCORABLE_SPANS,
    challenge,
    participant,
    span_challenge,
    submit,
)

LIMIT = 10
NOT_JSON = b"not json\n"


def submit_times(client, headers, challenge, times, **fields):
    """Submit ``times`` Submissions, asserting each one is accepted."""
    for _ in range(times):
        response = submit(client, headers, challenge, **fields)
        assert response.status_code == 202, response.text


def my_submissions(client, headers):
    response = client.get("/submissions/my", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_the_eleventh_submission_in_a_day_is_refused_with_the_reset_time(
    client, headers_for, participant, challenge, clock
):
    """After 10 stored Submissions on a Challenge in one UTC day, the next
    answers 429 with the reset time, the next 00:00 UTC."""
    clock.set(datetime.datetime(2026, 11, 26, 9, 30))
    headers = headers_for(participant)
    submit_times(client, headers, challenge, LIMIT)

    response = submit(client, headers, challenge)

    assert response.status_code == 429, response.text
    body = response.json()
    assert body["reset_at"] == "2026-11-27T00:00:00Z"
    assert "2026-11-27 00:00 UTC" in body["detail"]
    assert response.headers["Retry-After"] == str(14 * 3600 + 30 * 60)


def test_a_limited_submission_stores_nothing(client, headers_for, participant, challenge, storage):
    """A Submission refused by the limit stores no object and no Submission."""
    headers = headers_for(participant)
    submit_times(client, headers, challenge, LIMIT)
    stored_before = dict(storage.objects)

    response = submit(client, headers, challenge)

    assert response.status_code == 429, response.text
    assert storage.objects == stored_before
    assert len(my_submissions(client, headers)) == LIMIT


def test_the_limit_is_checked_before_validation(client, headers_for, participant, challenge):
    """At the limit, an invalid Submission is answered with the limit (429),
    not with its validation errors."""
    headers = headers_for(participant)
    submit_times(client, headers, challenge, LIMIT)

    response = submit(client, headers, challenge, predictions=NOT_JSON, name="")

    assert response.status_code == 429, response.text


def test_submissions_to_another_challenge_do_not_count(
    client, admin_headers, headers_for, participant, category, challenge
):
    """The limit is per Challenge: reaching it on one leaves another open."""
    other = create_challenge(client, admin_headers, category, title="Tibetan OCR, second edition").json()
    headers = headers_for(participant)
    submit_times(client, headers, challenge, LIMIT)

    response = submit(client, headers, other)

    assert response.status_code == 202, response.text


def test_another_participants_submissions_do_not_count(client, headers_for, make_user, participant, challenge):
    """The limit is per participant: someone else reaching it does not limit you."""
    submit_times(client, headers_for(make_user("user", name="other")), challenge, LIMIT)

    response = submit(client, headers_for(participant), challenge)

    assert response.status_code == 202, response.text


def test_submissions_that_failed_scoring_still_count(
    client, headers_for, participant, span_challenge, submission_worker
):
    """A stored Submission counts even when its scoring failed."""
    headers = headers_for(participant)
    submit_times(client, headers, span_challenge, LIMIT, predictions=UNSCORABLE_SPANS)
    submission_worker.drain()
    assert {s["status"] for s in my_submissions(client, headers)} == {"failed"}

    response = submit(client, headers, span_challenge, predictions=UNSCORABLE_SPANS)

    assert response.status_code == 429, response.text


def test_uploads_rejected_at_validation_do_not_count(client, headers_for, participant, challenge):
    """Rejected uploads are not stored, so they do not use up the limit."""
    headers = headers_for(participant)
    for _ in range(LIMIT):
        assert submit(client, headers, challenge, predictions=NOT_JSON).status_code == 422

    submit_times(client, headers, challenge, LIMIT)


def test_the_limit_resets_at_midnight_utc(client, headers_for, participant, challenge, clock):
    """Submissions from the previous UTC day no longer count from 00:00 UTC."""
    headers = headers_for(participant)
    clock.set(datetime.datetime(2026, 11, 26, 23, 59))
    submit_times(client, headers, challenge, LIMIT)

    clock.set(datetime.datetime(2026, 11, 27, 0, 0))
    response = submit(client, headers, challenge)

    assert response.status_code == 202, response.text


def test_the_day_is_the_utc_day(client, headers_for, participant, challenge, clock):
    """The day boundary is UTC midnight, not the local midnight of a time zone."""
    headers = headers_for(participant)
    india = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    # 01:00 on the 27th in India is still the 26th in UTC.
    clock.set(datetime.datetime(2026, 11, 26, 20, 0))
    submit_times(client, headers, challenge, LIMIT)

    clock.set(datetime.datetime(2026, 11, 27, 1, 0, tzinfo=india))
    response = submit(client, headers, challenge)

    assert response.status_code == 429, response.text
    assert response.json()["reset_at"] == "2026-11-27T00:00:00Z"


def test_admins_are_never_limited(client, headers_for, make_user, challenge):
    """An admin can store more than 10 Submissions on a Challenge in a day."""
    headers = headers_for(make_user("admin", name="baseline-admin"))

    submit_times(client, headers, challenge, LIMIT + 2)
