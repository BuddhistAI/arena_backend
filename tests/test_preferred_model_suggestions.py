"""Preferred Model suggestions: the Preferred Model ids already used on the
platform, which the submission form offers as autocomplete."""

from test_create_challenge import category, create_challenge  # noqa: F401  (category is a fixture)
from test_model_submission import challenge, participant, submit  # noqa: F401  (fixtures)
from test_prompt_submission import submit_prompt


def suggestions(client):
    response = client.get("/submissions/preferred-models")
    assert response.status_code == 200, response.text
    return response.json()


def test_ids_used_on_several_submissions_are_listed_once_each_in_order(
    client, admin_headers, headers_for, participant, category, challenge
):
    """Ids from Submissions on different Challenges are each listed once, sorted."""
    other_challenge = create_challenge(client, admin_headers, category, title="Another OCR").json()
    headers = headers_for(participant)
    for target, preferred_model in [
        (challenge, "gemini-2.5-pro"),
        (other_challenge, "claude-opus-4"),
        (other_challenge, "gemini-2.5-pro"),
    ]:
        response = submit_prompt(client, headers, target, preferred_model=preferred_model)
        assert response.status_code == 202, response.text

    assert suggestions(client) == ["claude-opus-4", "gemini-2.5-pro"]


def test_model_submissions_contribute_nothing(client, headers_for, participant, challenge):
    """A Model Submission's model name is not offered as a Preferred Model id."""
    headers = headers_for(participant)
    assert submit(client, headers, challenge, name="gpt-4o").status_code == 202
    assert submit_prompt(client, headers, challenge, preferred_model="gemini-2.5-pro").status_code == 202

    assert suggestions(client) == ["gemini-2.5-pro"]
