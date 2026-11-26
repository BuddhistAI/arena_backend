"""Submission detail: everything public about one Submission, at its own URL.
A scored Submission is public; an unscored one is shown only to its submitter
and admins; a deleted one answers 410 so a shared link can say so."""

import pytest

from test_create_challenge import category, create_challenge, jsonl  # noqa: F401  (category is a fixture)
from test_leaderboard import leaderboard, submit_scored
from test_model_submission import (  # noqa: F401  (challenge, participant, span_challenge are fixtures)
    UNSCORABLE_SPANS,
    challenge,
    participant,
    span_challenge,
    submit,
)
from test_prompt_submission import PROMPT, submit_prompt
from test_skill_submission import submit_skill


def detail(client, submission_id, headers=None):
    """The Submission's detail as ``headers`` sign in (anonymous by default)."""
    response = client.get(f"/submissions/{submission_id}", headers=headers or {})
    assert response.status_code == 200, response.text
    return response.json()


def test_a_visitor_reads_a_scored_model_submission_with_its_rank_and_submitter(
    client, headers_for, participant, challenge, submission_worker, clock
):
    """An anonymous visitor reads a scored Model Submission's Rank, type, name,
    submitter's username, Challenge and description."""
    submission_id = submit_scored(
        client, headers_for(participant), challenge, submission_worker, clock, model_link="https://example.org/ocr"
    )

    submission = detail(client, submission_id)

    assert submission["rank"] == 1
    assert submission["type"] == "model"
    assert submission["name"] == "Google Vision OCR"
    assert submission["submitter"] == "participant"
    assert submission["challenge_id"] == challenge["id"]
    assert submission["description"] == "Vision API, default settings."
    assert submission["model_link"] == "https://example.org/ocr"



def test_the_detail_shows_the_submitters_current_username_after_a_rename(
    client, headers_for, participant, challenge, submission_worker, clock
):
    """A submitter who renames is shown under the new username."""
    submission_id = submit_scored(client, headers_for(participant), challenge, submission_worker, clock)
    response = client.patch("/users/me", json={"username": "tenzin"}, headers=headers_for(participant))
    assert response.status_code == 200, response.text

    assert detail(client, submission_id)["submitter"] == "tenzin"

@pytest.mark.parametrize("who", ["anonymous", "submitter", "admin"])
def test_the_detail_never_shows_the_submitters_email_or_user_id(
    client, headers_for, participant, challenge, submission_worker, clock, admin_headers, who
):
    """Whoever reads it, the detail names the submitter only by public username."""
    submission_id = submit_scored(client, headers_for(participant), challenge, submission_worker, clock)
    headers = {"anonymous": {}, "submitter": headers_for(participant), "admin": admin_headers}[who]

    response = client.get(f"/submissions/{submission_id}", headers=headers)

    assert response.status_code == 200, response.text
    assert participant.email not in response.text
    assert participant.id not in response.text
    assert "user_id" not in response.json()


def test_the_detail_shows_the_headline_metric_and_public_summary_with_the_scorers_formats(
    client, admin_headers, headers_for, participant, category, submission_worker, clock
):
    """A WER Submission shows its Headline Metric, its Corpus WER, and the
    Scorer's label and display format to show them in."""
    wer = create_challenge(
        client, admin_headers, category,
        scorer_name="wer", scorer_params='{"language": "en"}',
        inputs=jsonl({"id": "a", "input": "clip a"}, {"id": "b", "input": "clip b"}),
        ground_truth=jsonl({"id": "a", "reference": "the cat sat"}, {"id": "b", "reference": "a dog"}),
    ).json()
    # Item a is exact (WER 0); item b gets one of its two words wrong (WER 1/2):
    # the mean is 1/4, and the corpus WER is 1 error in 5 words.
    submission_id = submit_scored(
        client, headers_for(participant), wer, submission_worker, clock,
        predictions=jsonl({"id": "a", "prediction": "the cat sat"}, {"id": "b", "prediction": "a cat"}),
    )

    submission = detail(client, submission_id)

    assert submission["headline_metric"] == 0.25
    assert submission["public_summary"] == [
        {"key": "corpus", "label": "Corpus WER", "value": 0.2, "format": {"style": "percent", "decimals": 2}}
    ]
    assert submission["scorer"]["label"] == wer["scorer_label"]
    assert submission["scorer"]["format"] == {"style": "percent", "decimals": 2}


def test_the_detail_shows_the_rank_the_leaderboard_shows(
    client, headers_for, participant, challenge, submission_worker, clock
):
    """Each Submission's detail has the Rank its Leaderboard row has."""
    headers = headers_for(participant)
    one_wrong = submit_scored(
        client, headers, challenge, submission_worker, clock,
        predictions=jsonl({"id": "1", "prediction": "ཀ"}, {"id": "2", "prediction": "ང"}),
    )
    exact = submit_scored(client, headers, challenge, submission_worker, clock)

    ranks = {row["submission_id"]: row["rank"] for row in leaderboard(client, challenge)["rows"]}

    assert ranks == {exact: 1, one_wrong: 2}
    assert (detail(client, exact)["rank"], detail(client, one_wrong)["rank"]) == (1, 2)


def test_a_prompts_detail_shows_its_text_preferred_model_and_license(
    client, headers_for, participant, challenge, submission_worker, clock
):
    """A scored Prompt's detail carries the prompt as typed, the model it was
    run with and its license, for anyone to read and copy."""
    submission_id = submit_scored(client, headers_for(participant), challenge, submission_worker, clock, submit_prompt)

    submission = detail(client, submission_id)

    assert submission["type"] == "prompt"
    assert submission["prompt_text"] == PROMPT
    assert submission["preferred_model"] == "gemini-2.5-pro"
    assert submission["license"] == "CC-BY-4.0"


def test_a_skills_detail_shows_its_preferred_model_and_license_and_no_prompt_text(
    client, headers_for, participant, challenge, submission_worker, clock
):
    """A scored Skill's detail names the model it was run with and its license;
    its files are downloaded, not shown inline."""
    submission_id = submit_scored(client, headers_for(participant), challenge, submission_worker, clock, submit_skill)

    submission = detail(client, submission_id)

    assert submission["type"] == "skill"
    assert (submission["preferred_model"], submission["license"]) == ("claude-sonnet-4-5", "MIT")
    assert submission["prompt_text"] is None


def test_a_submissions_repo_url_is_null_until_it_is_synced(
    client, headers_for, participant, challenge, submission_worker, clock
):
    """A scored Submission not yet in the Challenge Repo has no repo link."""
    submission_id = submit_scored(client, headers_for(participant), challenge, submission_worker, clock, submit_prompt)

    submission = detail(client, submission_id)

    assert (submission["sync_status"], submission["repo_url"]) == ("pending", None)


def test_a_synced_submissions_repo_url_points_to_its_folder_in_the_challenge_repo(
    client, headers_for, participant, challenge, submission_worker, clock, sync_worker, repo_host
):
    """Once synced, the repo link opens the Submission's folder on GitHub."""
    submission_id = submit_scored(client, headers_for(participant), challenge, submission_worker, clock, submit_prompt)
    sync_worker.drain()
    [prompt_file] = [path for path in repo_host.repos[challenge["repo_name"]].files if path.endswith("/prompt.md")]
    folder = prompt_file.removesuffix("/prompt.md")

    submission = detail(client, submission_id)

    assert submission["sync_status"] == "synced"
    assert submission["repo_url"] == f"https://github.com/BuddhistAI/{challenge['repo_name']}/tree/HEAD/{folder}"


@pytest.fixture
def failed_submission(client, headers_for, participant, span_challenge, submission_worker):
    """The id of ``participant``'s Submission whose scoring failed."""
    response = submit(client, headers_for(participant), span_challenge, predictions=UNSCORABLE_SPANS)
    assert response.status_code == 202, response.text
    submission_worker.drain()
    return response.json()["id"]


def test_a_submitter_reads_their_own_failed_submission_with_its_status_and_message(
    client, headers_for, participant, failed_submission
):
    """The submitter sees why their Submission failed, and that it has no Rank."""
    submission = detail(client, failed_submission, headers_for(participant))

    assert submission["status"] == "failed"
    assert "invalid span" in submission["status_message"]
    assert (submission["rank"], submission["headline_metric"]) == (None, None)


def test_an_admin_reads_anyones_failed_submission(client, admin_headers, failed_submission):
    """Admins see every unscored Submission's status and message."""
    submission = detail(client, failed_submission, admin_headers)

    assert submission["status"] == "failed"
    assert "invalid span" in submission["status_message"]


def test_an_anonymous_visitor_cannot_find_a_failed_submission(client, failed_submission):
    """To anyone signed out an unscored Submission does not exist."""
    response = client.get(f"/submissions/{failed_submission}")

    assert response.status_code == 404, response.text


def test_another_user_cannot_find_someone_elses_pending_submission(
    client, headers_for, participant, challenge, user_headers
):
    """Another signed-in user gets 404 for a Submission still waiting to be scored."""
    submission_id = submit(client, headers_for(participant), challenge).json()["id"]

    response = client.get(f"/submissions/{submission_id}", headers=user_headers)

    assert response.status_code == 404, response.text


@pytest.mark.parametrize("who", ["anonymous", "submitter", "admin"])
def test_a_deleted_submission_answers_410_to_everyone(
    client, headers_for, participant, challenge, submission_worker, clock, admin_headers, who
):
    """A shared link to a deleted Submission answers 410 Gone, so the page can
    say it was deleted rather than that it never existed."""
    submission_id = submit_scored(client, headers_for(participant), challenge, submission_worker, clock)
    assert client.delete(f"/submissions/{submission_id}", headers=headers_for(participant)).status_code == 204
    headers = {"anonymous": {}, "submitter": headers_for(participant), "admin": admin_headers}[who]

    response = client.get(f"/submissions/{submission_id}", headers=headers)

    assert response.status_code == 410, response.text
    assert response.json()["detail"] == "This Submission was deleted"


def test_an_unknown_submission_answers_404(client):
    """A Submission id nobody created answers 404."""
    response = client.get("/submissions/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404, response.text


def test_an_anonymous_visitor_cannot_find_a_failed_submissions_status(client, failed_submission):
    """Polling the status of someone's failed Submission while signed out
    answers 404, as its detail does: its message is not public."""
    response = client.get(f"/submissions/{failed_submission}/status")

    assert response.status_code == 404, response.text


def test_another_user_cannot_find_someone_elses_pending_submissions_status(
    client, headers_for, participant, challenge, user_headers
):
    """Another signed-in user gets 404 polling a Submission still waiting to be scored."""
    submission_id = submit(client, headers_for(participant), challenge).json()["id"]

    response = client.get(f"/submissions/{submission_id}/status", headers=user_headers)

    assert response.status_code == 404, response.text


@pytest.mark.parametrize("who", ["submitter", "admin"])
def test_the_submitter_and_admins_poll_a_failed_submissions_status_and_message(
    client, headers_for, participant, admin_headers, failed_submission, who
):
    """The submitter and admins see why scoring failed when they poll its status."""
    headers = {"submitter": headers_for(participant), "admin": admin_headers}[who]

    response = client.get(f"/submissions/{failed_submission}/status", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "failed"
    assert "invalid span" in response.json()["status_message"]


def test_a_visitor_polls_a_scored_submissions_status(
    client, headers_for, participant, challenge, submission_worker, clock
):
    """A scored Submission is public, so anyone may read its status."""
    submission_id = submit_scored(client, headers_for(participant), challenge, submission_worker, clock)

    response = client.get(f"/submissions/{submission_id}/status")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"


def test_a_deleted_submissions_status_answers_410(
    client, headers_for, participant, challenge, submission_worker, clock
):
    """Polling a deleted Submission's status answers 410 Gone, as its detail does."""
    submission_id = submit_scored(client, headers_for(participant), challenge, submission_worker, clock)
    assert client.delete(f"/submissions/{submission_id}", headers=headers_for(participant)).status_code == 204

    response = client.get(f"/submissions/{submission_id}/status", headers=headers_for(participant))

    assert response.status_code == 410, response.text
