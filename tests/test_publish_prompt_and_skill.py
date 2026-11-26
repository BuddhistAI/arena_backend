"""Publishing Prompt and Skill Submissions: once scored, a Prompt is written to
the Challenge Repo as ``prompts/<slug>-<id>/prompt.md`` and a Skill as its
extracted files under ``skills/<slug>-<id>/``, each next to its
``submission.json``, so others can read and reuse them on GitHub."""

import json

import pytest

from test_create_challenge import category, jsonl  # noqa: F401  (category is a fixture)
from test_model_submission import challenge, read  # noqa: F401  (challenge is a fixture)
from test_prompt_submission import PROMPT, submit_prompt
from test_skill_submission import make_zip, submit_skill

REPO = "challenge-tibetan-ocr"


@pytest.fixture
def participant(make_user):
    return make_user("user", name="tenzin")


@pytest.fixture
def scored(client, headers_for, participant, challenge, submission_worker):
    """``scored(submit, **fields)`` sends a Submission with ``submit`` as the
    participant, scores it and returns its id."""

    def _scored(submit, **fields):
        response = submit(client, headers_for(participant), challenge, **fields)
        assert response.status_code == 202, response.text
        submission_worker.drain()
        return response.json()["id"]

    return _scored


def folder_files(repo_host, folder):
    """The files under ``folder`` in the repo, by their path inside it."""
    prefix = f"{folder}/"
    return {
        path[len(prefix):]: content
        for path, content in repo_host.repos[REPO].files.items()
        if path.startswith(prefix)
    }


def test_a_typed_prompt_is_published_as_prompt_md_next_to_its_metadata(scored, repo_host, sync_worker):
    """A scored Prompt gets ``prompts/<slug>-<id>/`` holding ``prompt.md``,
    exactly as typed, and ``submission.json``."""
    submission_id = scored(submit_prompt, name="Careful transcriber")

    sync_worker.drain()

    files = folder_files(repo_host, f"prompts/careful-transcriber-{submission_id[:8]}")
    assert sorted(files) == ["prompt.md", "submission.json"]
    assert files["prompt.md"] == PROMPT.encode()


def test_an_uploaded_prompt_file_is_published_as_prompt_md_byte_for_byte(scored, repo_host, sync_worker):
    """An uploaded prompt lands as ``prompt.md`` too, whatever its content."""
    uploaded = "# བཀྲ་ཤིས།\r\n\r\nTranscribe {image}.".encode()
    submission_id = scored(submit_prompt, name="Careful transcriber", prompt_file=uploaded)

    sync_worker.drain()

    files = folder_files(repo_host, f"prompts/careful-transcriber-{submission_id[:8]}")
    assert sorted(files) == ["prompt.md", "submission.json"]
    assert files["prompt.md"] == uploaded


def test_a_prompts_metadata_names_its_preferred_model_and_license(
    challenge, scored, repo_host, sync_worker
):
    """A Prompt's ``submission.json`` holds its Preferred Model and license,
    and no model link."""
    submission_id = scored(
        submit_prompt, name="Careful transcriber", preferred_model="gemini-2.5-pro", license="CC0-1.0"
    )

    sync_worker.drain()

    files = folder_files(repo_host, f"prompts/careful-transcriber-{submission_id[:8]}")
    assert json.loads(files["submission.json"]) == {
        "schema_version": 1,
        "submission_id": submission_id,
        "challenge_id": challenge["id"],
        "type": "prompt",
        "name": "Careful transcriber",
        "description": "System prompt; the image is the user message. Temperature 0.",
        "submitter": "tenzin",
        "scorer_name": "cer",
        "headline_metric": 0.0,
        "public_summary": [
            {"key": "corpus", "label": "Corpus CER", "value": 0.0, "format": {"style": "percent", "decimals": 2}}
        ],
        "preferred_model": "gemini-2.5-pro",
        "model_link": None,
        "license": "CC0-1.0",
        "created_at": "2026-11-26T12:00:00Z",
    }


def test_a_skill_is_published_as_its_extracted_files_at_their_paths_in_the_zip(
    scored, repo_host, sync_worker
):
    """A scored Skill gets ``skills/<slug>-<id>/`` holding each file of its
    zip at the same relative path, top folder included, plus ``submission.json``."""
    skill_zip = make_zip(
        ("tibetan-ocr/SKILL.md", "# Tibetan OCR\n"),
        ("tibetan-ocr/scripts/run.py", "print('ཀ')\n"),
        ("NOTES.txt", "Top-level note.\n"),
    )
    submission_id = scored(submit_skill, name="Tibetan OCR skill", skill_zip=skill_zip)

    sync_worker.drain()

    files = folder_files(repo_host, f"skills/tibetan-ocr-skill-{submission_id[:8]}")
    assert files == {
        "tibetan-ocr/SKILL.md": b"# Tibetan OCR\n",
        "tibetan-ocr/scripts/run.py": "print('ཀ')\n".encode(),
        "NOTES.txt": b"Top-level note.\n",
        "submission.json": files["submission.json"],
    }


def skill_folder_files(scored, repo_host, sync_worker, skill_zip):
    """Score and publish a Skill with ``skill_zip``; the files of its folder."""
    submission_id = scored(submit_skill, name="Tibetan OCR skill", skill_zip=skill_zip)
    sync_worker.drain()
    return folder_files(repo_host, f"skills/tibetan-ocr-skill-{submission_id[:8]}")


def test_backslashes_in_a_skills_entry_names_are_published_as_slashes(scored, repo_host, sync_worker):
    """A zip made on Windows, with ``\\`` separators, publishes into folders."""
    skill_zip = make_zip(("scripts\\ocr\\run.py", "print('ཀ')\n"))

    files = skill_folder_files(scored, repo_host, sync_worker, skill_zip)

    assert sorted(files) == ["scripts/ocr/run.py", "submission.json"]


def test_macos_metadata_in_a_skill_zip_is_not_published(scored, repo_host, sync_worker):
    """Finder's ``__MACOSX/`` folder and ``._*`` AppleDouble files are left out;
    the real files next to them are published."""
    skill_zip = make_zip(
        ("tibetan-ocr/SKILL.md", "# Tibetan OCR\n"),
        ("__MACOSX/tibetan-ocr/._SKILL.md", b"\x00\x05\x16\x07"),
        ("tibetan-ocr/._run.py", b"\x00\x05\x16\x07"),
        ("tibetan-ocr/run.py", "print('ཀ')\n"),
    )

    files = skill_folder_files(scored, repo_host, sync_worker, skill_zip)

    assert sorted(files) == ["submission.json", "tibetan-ocr/SKILL.md", "tibetan-ocr/run.py"]


@pytest.mark.parametrize("skill_zip", [make_zip(), make_zip(("empty/", ""), ("empty/nested/", ""))])
def test_a_skill_zip_with_no_files_publishes_only_its_metadata(scored, repo_host, sync_worker, skill_zip):
    """Git holds no empty folders, so a zip of only folders, or of nothing,
    publishes just ``submission.json``."""
    files = skill_folder_files(scored, repo_host, sync_worker, skill_zip)

    assert sorted(files) == ["submission.json"]


def test_a_skills_metadata_names_its_preferred_model_and_license(challenge, scored, repo_host, sync_worker):
    """A Skill's ``submission.json`` holds its Preferred Model and license,
    and no model link."""
    submission_id = scored(submit_skill, preferred_model="claude-sonnet-4-5", license="Apache-2.0")

    sync_worker.drain()

    files = folder_files(repo_host, f"skills/tibetan-ocr-skill-{submission_id[:8]}")
    assert json.loads(files["submission.json"]) == {
        "schema_version": 1,
        "submission_id": submission_id,
        "challenge_id": challenge["id"],
        "type": "skill",
        "name": "Tibetan OCR skill",
        "description": "Load the skill in Claude Code and run it on each image. Temperature 0.",
        "submitter": "tenzin",
        "scorer_name": "cer",
        "headline_metric": 0.0,
        "public_summary": [
            {"key": "corpus", "label": "Corpus CER", "value": 0.0, "format": {"style": "percent", "decimals": 2}}
        ],
        "preferred_model": "claude-sonnet-4-5",
        "model_link": None,
        "license": "Apache-2.0",
        "created_at": "2026-11-26T12:00:00Z",
    }


def test_the_skill_zip_itself_is_never_written_to_the_repo(scored, repo_host, sync_worker):
    """Only the extracted files are published; no ``.zip`` lands in the repo."""
    scored(submit_skill)

    sync_worker.drain()

    assert not any(path.endswith(".zip") for path in repo_host.repos[REPO].files)


@pytest.mark.parametrize("submit", [submit_prompt, submit_skill])
def test_predictions_of_prompts_and_skills_are_never_written_to_the_repo(scored, repo_host, sync_worker, submit):
    """No file in the repo holds a Prompt's or a Skill's Predictions."""
    scored(submit, predictions=jsonl({"id": "1", "prediction": "PRED-ONE"}, {"id": "2", "prediction": "PRED-TWO"}))

    sync_worker.drain()

    published = b"".join(repo_host.repos[REPO].files.values()).decode()
    assert "PRED-ONE" not in published
    assert "PRED-TWO" not in published


@pytest.mark.parametrize("submit", [submit_prompt, submit_skill])
def test_a_prompt_or_skill_whose_sync_failed_is_published_by_a_resync(
    client, admin_headers, scored, repo_host, sync_worker, submit
):
    """GitHub failures and Resync work as for Model Submissions: ``failed``
    after 5 attempts, then published with its Artifact after a Resync."""
    sync_worker.drain()
    submission_id = scored(submit, name="Retried")
    repo_host.fail_next(5, "GitHub is down")
    sync_worker.drain()
    assert read(client, submission_id)["sync_status"] == "failed"

    response = client.post(f"/submissions/{submission_id}/resync", headers=admin_headers)
    assert response.status_code == 200, response.text
    sync_worker.drain()

    submission = read(client, submission_id)
    assert submission["sync_status"] == "synced"
    files = folder_files(repo_host, submission["repo_path"])
    assert "submission.json" in files
    assert len(files) > 1


def lose_artifact(storage, submission_id):
    """Remove a Submission's Artifact from storage, as if it had been lost."""
    [key] = [
        key for key in storage.objects
        if f"/submissions/{submission_id}/" in key and not key.endswith("predictions.jsonl")
    ]
    storage.delete(key)


@pytest.mark.parametrize("submit", [submit_prompt, submit_skill])
def test_a_submission_whose_artifact_is_missing_is_skipped_and_the_others_still_publish(
    client, scored, storage, repo_host, sync_worker, submit
):
    """A lost Artifact fails only its own Submission's sync: it gets no
    folder, while the other Submissions are published and ``synced``."""
    lost = scored(submit, name="Lost")
    kept = scored(submit_prompt, name="Kept")
    lose_artifact(storage, lost)

    sync_worker.drain()

    folders = {path.split("/")[1] for path in repo_host.repos[REPO].files if path.startswith(("prompts/", "skills/"))}
    assert folders - {"README.md"} == {f"kept-{kept[:8]}"}
    assert (read(client, lost)["sync_status"], read(client, lost)["repo_path"]) == ("failed", None)
    assert read(client, kept)["sync_status"] == "synced"
