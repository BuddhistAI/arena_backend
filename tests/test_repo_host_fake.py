"""The in-memory repo host honours the challenge repo host port's contract."""

import pytest

from ports.repo_host import RepoAlreadyExists, RepoHostError, RepoNotFound


def test_a_repo_created_from_the_template_has_the_template_layout(repo_host):
    repo_host.create_repo_from_template("challenge-tibetan-ocr", description="Tibetan OCR")

    repo = repo_host.repos["challenge-tibetan-ocr"]
    assert repo.description == "Tibetan OCR"
    assert {"models/README.md", "prompts/README.md", "skills/README.md"} <= set(repo.files)
    assert repo.archived is False


def test_creating_a_repo_twice_is_refused(repo_host):
    repo_host.create_repo_from_template("challenge-x")

    with pytest.raises(RepoAlreadyExists):
        repo_host.create_repo_from_template("challenge-x")


def test_files_are_written_in_one_commit(repo_host):
    repo_host.create_repo_from_template("challenge-x")
    repo_host.write_files(
        "challenge-x",
        {"challenge.json": '{"id": "c1"}', "data/inputs.jsonl": b'{"id": "1"}\n'},
        message="Sync challenge",
    )

    repo = repo_host.repos["challenge-x"]
    assert repo.files["challenge.json"] == b'{"id": "c1"}'
    assert repo.files["data/inputs.jsonl"] == b'{"id": "1"}\n'
    assert repo.commits[-1].message == "Sync challenge"
    assert set(repo.commits[-1].files) == {"challenge.json", "data/inputs.jsonl"}


def test_writing_to_a_missing_repo_fails(repo_host):
    with pytest.raises(RepoNotFound):
        repo_host.write_files("challenge-missing", {"a.txt": "a"}, message="m")


def test_an_archived_repo_is_marked_archived(repo_host):
    repo_host.create_repo_from_template("challenge-x")
    repo_host.archive_repo("challenge-x")

    assert repo_host.repos["challenge-x"].archived is True


def test_the_fake_fails_the_next_n_calls_then_recovers(repo_host):
    repo_host.fail_next(2, message="GitHub is down")

    for _ in range(2):
        with pytest.raises(RepoHostError, match="GitHub is down"):
            repo_host.create_repo_from_template("challenge-x")
    assert repo_host.repos == {}

    repo_host.create_repo_from_template("challenge-x")
    assert "challenge-x" in repo_host.repos


def test_every_kind_of_call_can_be_made_to_fail(repo_host):
    repo_host.create_repo_from_template("challenge-x")
    repo_host.fail_next(2)

    with pytest.raises(RepoHostError):
        repo_host.write_files("challenge-x", {"a.txt": "a"}, message="m")
    with pytest.raises(RepoHostError):
        repo_host.archive_repo("challenge-x")

    repo = repo_host.repos["challenge-x"]
    assert "a.txt" not in repo.files
    assert repo.archived is False
