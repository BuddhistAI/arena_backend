"""Listing every Submission is for admins: it shows each Submission's
submitter and repo folder, so participants only see their own (``/my``).
An admin can narrow the list to one Challenge."""

from test_create_challenge import category, create_challenge  # noqa: F401  (category is a fixture)
from test_model_submission import challenge, participant, submit  # noqa: F401  (fixtures)


def test_a_participant_cannot_list_every_submission(client, headers_for, participant, challenge):
    """A signed-in user who is not an admin gets 403 from the full listing."""
    submit(client, headers_for(participant), challenge)

    response = client.get("/submissions", headers=headers_for(participant))

    assert response.status_code == 403, response.text


def test_an_anonymous_visitor_cannot_list_every_submission(client, headers_for, participant, challenge):
    """A visitor who is not signed in is refused the full listing."""
    submit(client, headers_for(participant), challenge)

    response = client.get("/submissions")

    assert response.status_code in (401, 403), response.text


def test_an_admin_lists_every_users_submissions(client, headers_for, admin_headers, make_user, challenge):
    """An admin sees the Submissions of every participant."""
    first, second = make_user("user", name="first"), make_user("user", name="second")
    submit(client, headers_for(first), challenge)
    submit(client, headers_for(second), challenge)

    response = client.get("/submissions", headers=admin_headers)

    assert response.status_code == 200, response.text
    assert sorted(s["user_id"] for s in response.json()) == [first.id, second.id]


def test_an_admin_lists_the_submissions_of_one_challenge(
    client, headers_for, admin_headers, participant, category, challenge
):
    """With ``challenge_id`` the listing holds only that Challenge's Submissions."""
    other = create_challenge(client, admin_headers, category, title="Tibetan OCR 2").json()
    wanted = submit(client, headers_for(participant), challenge).json()["id"]
    submit(client, headers_for(participant), other)

    response = client.get("/submissions", params={"challenge_id": challenge["id"]}, headers=admin_headers)

    assert response.status_code == 200, response.text
    assert [s["id"] for s in response.json()] == [wanted]
