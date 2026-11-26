"""Downloads: anyone, signed in or not, downloads a scored Prompt or Skill from
our storage, named after its folder in the Challenge Repo. GitHub is never on
the download path, so a pending or failed sync does not block it."""

import pytest

from test_create_challenge import category  # noqa: F401  (category is a fixture)
from test_leaderboard import submit_scored
from test_model_submission import challenge, participant  # noqa: F401  (fixtures)
from test_prompt_submission import PROMPT, submit_prompt
from test_skill_submission import SKILL_ZIP, submit_skill


def download(client, submission_id):
    """The download's redirect target, followed by hand: anonymous, no sign-in."""
    response = client.get(f"/submissions/{submission_id}/download", follow_redirects=False)
    assert response.status_code in (302, 307), response.text
    return response.headers["location"]


def served(storage, location):
    """What the presigned URL ``location`` serves: the stored bytes and the
    file name the download is saved under."""
    [presigned] = [entry for entry in storage.presigned_downloads if entry.url == location]
    return storage.get(presigned.key), presigned.file_name


def test_a_visitor_downloads_a_prompt_as_slug_and_id_dot_md(
    client, headers_for, participant, challenge, submission_worker, clock, storage
):
    """A scored Prompt downloads as its text, named ``<slug>-<id>.md``."""
    submission_id = submit_scored(client, headers_for(participant), challenge, submission_worker, clock, submit_prompt)

    content, file_name = served(storage, download(client, submission_id))

    assert content == PROMPT.encode()
    assert file_name == f"careful-transcriber-{submission_id[:8]}.md"


def test_a_visitor_downloads_a_skill_as_its_uploaded_zip_named_slug_and_id_dot_zip(
    client, headers_for, participant, challenge, submission_worker, clock, storage
):
    """A scored Skill downloads as exactly the zip its author uploaded, named ``<slug>-<id>.zip``."""
    submission_id = submit_scored(client, headers_for(participant), challenge, submission_worker, clock, submit_skill)

    content, file_name = served(storage, download(client, submission_id))

    assert content == SKILL_ZIP
    assert file_name == f"tibetan-ocr-skill-{submission_id[:8]}.zip"


def test_a_synced_prompt_downloads_under_its_folders_name_in_the_challenge_repo(
    client, headers_for, participant, challenge, submission_worker, clock, storage, sync_worker, repo_host
):
    """The download's ``<slug>-<id>`` is the Submission's folder in the Challenge Repo."""
    submission_id = submit_scored(client, headers_for(participant), challenge, submission_worker, clock, submit_prompt)
    sync_worker.drain()
    [prompt_file] = [path for path in repo_host.repos[challenge["repo_name"]].files if path.endswith("/prompt.md")]
    folder = prompt_file.removesuffix("/prompt.md").rsplit("/", 1)[1]

    _, file_name = served(storage, download(client, submission_id))

    assert file_name == f"{folder}.md"


def test_a_model_submission_has_no_download(client, headers_for, participant, challenge, submission_worker, clock):
    """A Model is found at its own host, not downloaded from us."""
    submission_id = submit_scored(client, headers_for(participant), challenge, submission_worker, clock)

    response = client.get(f"/submissions/{submission_id}/download", follow_redirects=False)

    assert response.status_code == 404, response.text


def assert_no_download(client, submission_id):
    response = client.get(f"/submissions/{submission_id}/download", follow_redirects=False)
    assert response.status_code == 404, response.text


@pytest.mark.parametrize("submit", [submit_prompt, submit_skill])
def test_a_deleted_prompt_or_skill_has_no_download(
    client, headers_for, participant, challenge, submission_worker, clock, submit
):
    """Removed material is not served."""
    submission_id = submit_scored(client, headers_for(participant), challenge, submission_worker, clock, submit)
    assert client.delete(f"/submissions/{submission_id}", headers=headers_for(participant)).status_code == 204

    assert_no_download(client, submission_id)


@pytest.mark.parametrize("submit", [submit_prompt, submit_skill])
def test_an_unscored_prompt_or_skill_has_no_download(client, headers_for, participant, challenge, submit):
    """A Submission still waiting to be scored cannot be downloaded."""
    response = submit(client, headers_for(participant), challenge)
    assert response.status_code == 202, response.text

    assert_no_download(client, response.json()["id"])


def test_an_unknown_submission_has_no_download(client):
    assert_no_download(client, "00000000-0000-0000-0000-000000000000")


@pytest.mark.parametrize("submit", [submit_prompt, submit_skill])
def test_a_download_works_before_the_github_sync_and_after_it_fails(
    client, headers_for, participant, challenge, submission_worker, clock, storage, sync_worker, repo_host, submit
):
    """GitHub is never on the download path: pending or failed, the file still comes from storage."""
    submission_id = submit_scored(client, headers_for(participant), challenge, submission_worker, clock, submit)
    assert client.get(f"/submissions/{submission_id}").json()["sync_status"] == "pending"
    pending_content, _ = served(storage, download(client, submission_id))

    repo_host.fail_next(5, "GitHub is down")
    sync_worker.drain()
    assert client.get(f"/submissions/{submission_id}").json()["sync_status"] == "failed"
    failed_content, _ = served(storage, download(client, submission_id))

    assert pending_content == failed_content == (PROMPT.encode() if submit is submit_prompt else SKILL_ZIP)

