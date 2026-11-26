"""Sync Worker: after a Challenge is created, the worker publishes its
Challenge Repo (challenge.json, README, Inputs, sample Predictions File) to
the repo host, retrying with backoff and recording the outcome on the
Challenge. Tests drain the worker synchronously."""

import json

import pytest

from test_create_challenge import (  # noqa: F401  (category is a fixture)
    admin_read,
    OCR_GROUND_TRUTH,
    OCR_INPUTS,
    category,
    create_challenge,
    jsonl,
    zip_of,
)


def created(response):
    assert response.status_code == 201, response.text
    return response.json()


read = admin_read


def test_a_created_challenge_is_published_to_its_repo_with_challenge_json(
    client, admin_headers, category, repo_host, storage, sync_worker
):
    challenge = created(create_challenge(
        client, admin_headers, category, title="Tibetan OCR", scorer_name="cer", scorer_params="{}"
    ))

    sync_worker.drain()

    repo = repo_host.repos["challenge-tibetan-ocr"]
    assert json.loads(repo.files["challenge.json"]) == {
        "schema_version": 1,
        "id": challenge["id"],
        "title": "Tibetan OCR",
        "description": "Read the woodblock.",
        "category": "OCR",
        "scorer": {
            "name": "cer",
            "label": "Character Error Rate (CER)",
            "direction": "lower_is_better",
            "params": {"language": "bo"},
            "package_version": "0.3.0",
        },
        "data": {
            "id_field": "id",
            "inputs": "data/inputs.jsonl",
            "inputs_url": None,
            "media_url": None,
            "sample_submission": "data/sample_submission.jsonl",
        },
        "submission_format": {"file_type": "jsonl", "fields": ["id", "prediction"]},
    }
    synced = read(client, challenge)
    assert synced["sync_status"] == "synced"
    assert synced["last_sync_error"] is None


def test_the_repo_holds_the_inputs_as_uploaded(client, admin_headers, category, repo_host, sync_worker):
    inputs = jsonl({"id": "p2", "input": "བཀྲ་ཤིས་"}, {"id": "p1", "input": "y"})
    ground_truth = jsonl({"id": "p1", "reference": "ཀ"}, {"id": "p2", "reference": "ཁ"})
    created(create_challenge(client, admin_headers, category, inputs=inputs, ground_truth=ground_truth))

    sync_worker.drain()

    assert repo_host.repos["challenge-tibetan-ocr"].files["data/inputs.jsonl"] == inputs


@pytest.mark.parametrize(
    "scorer_name, inputs, ground_truth, ids_in_file_order, placeholder",
    [
        pytest.param(
            "cer",
            jsonl({"id": "p2", "input": "x"}, {"id": "p1", "input": "y"}),
            jsonl({"id": "p1", "reference": "ཀ"}, {"id": "p2", "reference": "ཁ"}),
            ["p2", "p1"],
            "",
            id="text",
        ),
        pytest.param(
            "span_f1",
            jsonl({"id": "s2", "input": "om mani"}, {"id": "s1", "input": "padme hum"}),
            jsonl(
                {"id": "s1", "reference": [{"start": 0, "end": 1, "tag": "mantra"}]},
                {"id": "s2", "reference": []},
            ),
            ["s2", "s1"],
            [],
            id="spans",
        ),
    ],
)
def test_the_sample_predictions_file_has_one_placeholder_row_per_input_id(
    client, admin_headers, category, repo_host, sync_worker,
    scorer_name, inputs, ground_truth, ids_in_file_order, placeholder,
):
    created(create_challenge(
        client, admin_headers, category, scorer_name=scorer_name, inputs=inputs, ground_truth=ground_truth
    ))

    sync_worker.drain()

    sample = repo_host.repos["challenge-tibetan-ocr"].files["data/sample_submission.jsonl"]
    rows = [json.loads(line) for line in sample.decode().splitlines()]
    assert rows == [{"id": i, "prediction": placeholder} for i in ids_in_file_order]


def test_inputs_over_the_commit_limit_are_linked_from_challenge_json_not_committed(
    client, admin_headers, category, repo_host, storage, make_sync_worker
):
    inputs = jsonl({"id": "1", "input": "x" * 200}, {"id": "2", "input": "y"})
    challenge = created(create_challenge(client, admin_headers, category, inputs=inputs))
    # The real limit is 50 MB; the test lowers it rather than upload 50 MB.
    worker = make_sync_worker(max_committed_inputs_bytes=len(inputs) - 1)

    worker.drain()

    repo = repo_host.repos["challenge-tibetan-ocr"]
    data = json.loads(repo.files["challenge.json"])["data"]
    assert data["inputs"] is None
    assert data["inputs_url"] == storage.public_url(challenge["inputs_key"])
    assert all(inputs not in commit.files.values() for commit in repo.commits)
    assert "data/inputs.jsonl" not in [path for commit in repo.commits for path in commit.files]
    # The sample Predictions File is still committed.
    sample = repo.files["data/sample_submission.jsonl"].decode().splitlines()
    assert [json.loads(row)["id"] for row in sample] == ["1", "2"]


def test_inputs_at_the_commit_limit_are_committed(
    client, admin_headers, category, repo_host, make_sync_worker
):
    inputs = jsonl({"id": "1", "input": "x"}, {"id": "2", "input": "y"})
    created(create_challenge(client, admin_headers, category, inputs=inputs))

    make_sync_worker(max_committed_inputs_bytes=len(inputs)).drain()

    repo = repo_host.repos["challenge-tibetan-ocr"]
    assert repo.files["data/inputs.jsonl"] == inputs
    assert json.loads(repo.files["challenge.json"])["data"]["inputs_url"] is None


def test_a_media_archive_is_linked_from_challenge_json_not_committed(
    client, admin_headers, category, repo_host, storage, sync_worker
):
    archive = zip_of("images/p1.png", "images/p2.png")
    challenge = created(create_challenge(
        client, admin_headers, category,
        inputs=OCR_INPUTS, ground_truth=OCR_GROUND_TRUTH, media_archive=archive,
    ))

    sync_worker.drain()

    repo = repo_host.repos["challenge-tibetan-ocr"]
    data = json.loads(repo.files["challenge.json"])["data"]
    assert data["media_url"] == storage.public_url(challenge["media_archive_key"])
    assert all(archive not in commit.files.values() for commit in repo.commits)


def test_the_ground_truth_is_in_no_file_sent_to_the_repo_host(
    client, admin_headers, category, repo_host, sync_worker
):
    ground_truth = jsonl(
        {"id": "1", "reference": "SECRET-REFERENCE-ONE"},
        {"id": "2", "reference": "SECRET-REFERENCE-TWO"},
    )
    created(create_challenge(client, admin_headers, category, ground_truth=ground_truth))

    sync_worker.drain()

    repo = repo_host.repos["challenge-tibetan-ocr"]
    sent = [content for commit in repo.commits for content in commit.files.values()]
    assert sent
    for content in sent + list(repo.files.values()):
        assert b"SECRET-REFERENCE" not in content


def test_the_readme_explains_the_challenge_and_how_to_take_part(
    client, admin_headers, category, repo_host, sync_worker
):
    created(create_challenge(
        client, admin_headers, category,
        title="Span tagging", scorer_name="span_f1", scorer_params='{"iou_threshold": 0.75}',
        inputs=jsonl({"id": "s1", "input": "om mani padme hum"}),
        ground_truth=jsonl({"id": "s1", "reference": [{"start": 0, "end": 1, "tag": "mantra"}]}),
    ))

    sync_worker.drain()

    readme = repo_host.repos["challenge-span-tagging"].files["README.md"].decode()
    assert readme.startswith("# Span tagging\n")
    assert "Read the woodblock." in readme
    assert "Category: OCR" in readme
    assert "`span_f1`" in readme
    assert "higher is better" in readme
    assert '"iou_threshold": 0.75' in readme
    assert "buddhist-eval 0.3.0" in readme
    assert "`data/inputs.jsonl`" in readme
    assert "`data/sample_submission.jsonl`" in readme
    assert "Ground Truth" in readme


def test_the_readme_links_inputs_that_are_too_large_to_commit(
    client, admin_headers, category, repo_host, storage, make_sync_worker
):
    inputs = jsonl({"id": "1", "input": "x" * 200}, {"id": "2", "input": "y"})
    challenge = created(create_challenge(client, admin_headers, category, inputs=inputs))

    make_sync_worker(max_committed_inputs_bytes=len(inputs) - 1).drain()

    readme = repo_host.repos["challenge-tibetan-ocr"].files["README.md"].decode()
    assert storage.public_url(challenge["inputs_key"]) in readme
    assert "`data/inputs.jsonl`" not in readme


def test_a_failed_sync_stays_pending_with_its_error_and_is_retried_until_it_succeeds(
    client, admin_headers, category, repo_host, sync_worker
):
    challenge = created(create_challenge(client, admin_headers, category))
    repo_host.fail_next(1, "GitHub is down")

    sync_worker.run_once()

    retrying = read(client, challenge)
    assert retrying["sync_status"] == "pending"
    assert retrying["sync_attempts"] == 1
    assert "GitHub is down" in retrying["last_sync_error"]

    repo_host.fail_next(2, "GitHub is down")
    sync_worker.drain()

    synced = read(client, challenge)
    assert synced["sync_status"] == "synced"
    assert synced["sync_attempts"] == 0
    assert synced["last_sync_error"] is None
    assert "challenge.json" in repo_host.repos["challenge-tibetan-ocr"].files


def test_a_sync_that_fails_five_times_is_marked_failed_with_the_last_error(
    client, admin_headers, category, repo_host, sync_worker
):
    challenge = created(create_challenge(client, admin_headers, category))
    repo_host.fail_next(4, "GitHub is down")
    sync_worker.run_once()  # the first failure
    repo_host.fail_next(4, "Bad credentials")

    sync_worker.drain()

    failed = read(client, challenge)
    assert failed["sync_status"] == "failed"
    assert failed["sync_attempts"] == 5
    assert "Bad credentials" in failed["last_sync_error"]
    assert repo_host.repos == {}

    # A failed Challenge is left alone until an admin asks for a Resync.
    sync_worker.drain()
    assert repo_host.repos == {}
    assert read(client, challenge)["sync_status"] == "failed"


def test_retries_back_off_exponentially(client, admin_headers, category, repo_host, make_sync_worker):
    from datetime import datetime, timedelta

    now = datetime(2026, 1, 1, 12, 0, 0)
    worker = make_sync_worker(clock=lambda: now, backoff=timedelta(seconds=30))
    challenge = created(create_challenge(client, admin_headers, category))
    repo_host.fail_next(2)

    worker.run_once()  # attempt 1 fails at 12:00:00
    assert read(client, challenge)["sync_attempts"] == 1

    now += timedelta(seconds=29)
    worker.run_once()  # not due before 30 s
    assert read(client, challenge)["sync_attempts"] == 1

    now += timedelta(seconds=1)
    worker.run_once()  # attempt 2 fails at 12:00:30
    assert read(client, challenge)["sync_attempts"] == 2

    now += timedelta(seconds=59)
    worker.run_once()  # not due before 60 s
    assert read(client, challenge)["sync_attempts"] == 2

    now += timedelta(seconds=1)
    worker.run_once()  # attempt 3 succeeds
    assert read(client, challenge)["sync_status"] == "synced"


def test_a_challenge_with_a_tibetan_title_is_published_under_its_fallback_repo_name(
    client, admin_headers, category, repo_host, sync_worker
):
    challenge = created(create_challenge(client, admin_headers, category, title="བོད་ཡིག་ཀློག་པ།"))

    sync_worker.drain()

    assert list(repo_host.repos) == [challenge["repo_name"]]
    repo = repo_host.repos[challenge["repo_name"]]
    assert json.loads(repo.files["challenge.json"])["title"] == "བོད་ཡིག་ཀློག་པ།"
    assert repo.files["README.md"].decode().startswith("# བོད་ཡིག་ཀློག་པ།\n")


def test_an_edit_after_a_sync_is_published_to_the_same_repo(
    client, admin_headers, category, repo_host, sync_worker
):
    challenge = created(create_challenge(client, admin_headers, category, title="Tibetan OCR"))
    sync_worker.drain()
    new_inputs = jsonl({"id": "1", "input": "new x"}, {"id": "2", "input": "new y"})

    response = client.patch(
        f"/challenges/update/{challenge['id']}",
        data={"title": "Tibetan OCR, revised", "description": "Read the manuscript."},
        files={"inputs_file": ("inputs.jsonl", new_inputs, "application/jsonl")},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    sync_worker.drain()

    assert list(repo_host.repos) == ["challenge-tibetan-ocr"]  # the repo keeps its name
    repo = repo_host.repos["challenge-tibetan-ocr"]
    definition = json.loads(repo.files["challenge.json"])
    assert (definition["title"], definition["description"]) == ("Tibetan OCR, revised", "Read the manuscript.")
    assert repo.files["data/inputs.jsonl"] == new_inputs
    assert len(repo.commits) == 2
    assert read(client, challenge)["sync_status"] == "synced"


def test_the_repo_keeps_the_template_layout_and_gitignore(
    client, admin_headers, category, repo_host, sync_worker
):
    created(create_challenge(client, admin_headers, category, title="Tibetan OCR"))

    sync_worker.drain()

    files = repo_host.repos["challenge-tibetan-ocr"].files
    assert set(files) == {
        ".gitignore",
        "README.md",
        "challenge.json",
        "data/README.md",
        "data/inputs.jsonl",
        "data/sample_submission.jsonl",
        "models/README.md",
        "prompts/README.md",
        "skills/README.md",
    }
    assert b"*ground_truth*" in files[".gitignore"]
    assert files["models/README.md"].startswith(b"# Model submissions\n")
    assert files["prompts/README.md"].startswith(b"# Prompt submissions\n")
    assert files["skills/README.md"].startswith(b"# Skill submissions\n")
    assert b"sample_submission.jsonl" in files["data/README.md"]
