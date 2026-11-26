"""Create Challenge: an admin uploads Inputs, Ground Truth and an optional
Media Archive, picks a Scorer and its parameters, and the arena validates and
stores them."""

import json

import pytest


def jsonl(*rows):
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows).encode()


@pytest.fixture
def category(db, make_user):
    from models import Category

    owner = make_user("admin", name="category-owner")
    category = Category(name="OCR", created_by=owner.id, updated_by=owner.id)
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


def create_challenge(
    client,
    headers,
    category,
    *,
    title="Tibetan OCR",
    scorer_name="cer",
    scorer_params="{}",
    inputs=None,
    ground_truth=None,
    media_archive=None,
):
    inputs = jsonl({"id": "1", "input": "x"}, {"id": "2", "input": "y"}) if inputs is None else inputs
    ground_truth = (
        jsonl({"id": "1", "reference": "ཀ"}, {"id": "2", "reference": "ཁ"})
        if ground_truth is None
        else ground_truth
    )
    files = {
        "inputs_file": ("inputs.jsonl", inputs, "application/jsonl"),
        "ground_truth_file": ("ground_truth.jsonl", ground_truth, "application/jsonl"),
    }
    if media_archive is not None:
        files["media_archive_file"] = ("media.zip", media_archive, "application/zip")
    data = {
        "title": title,
        "category_id": str(category.id),
        "description": "Read the woodblock.",
        "image_uri": "https://img.test/ocr.png",
        "scorer_name": scorer_name,
        "scorer_params": scorer_params,
    }
    return client.post("/challenges/create", data=data, files=files, headers=headers)


def test_signed_in_non_admin_is_not_allowed_to_create(client, user_headers, category, storage):
    response = create_challenge(client, user_headers, category)

    assert response.status_code == 403, response.text
    assert "admin" in response.json()["detail"].lower()
    assert storage.objects == {}
    assert client.get("/challenges/list").json() == []


def test_admin_creates_a_challenge_whose_files_are_stored_and_sync_is_pending(
    client, admin_headers, category, storage, repo_host
):
    inputs = jsonl({"id": "1", "input": "x"}, {"id": "2", "input": "y"})
    ground_truth = jsonl({"id": "1", "reference": "ཀ"}, {"id": "2", "reference": "ཁ"})

    response = create_challenge(
        client,
        admin_headers,
        category,
        title="Tibetan OCR: Woodblocks!",
        inputs=inputs,
        ground_truth=ground_truth,
    )

    assert response.status_code == 201, response.text
    created = response.json()
    assert created["title"] == "Tibetan OCR: Woodblocks!"
    assert created["scorer_name"] == "cer"
    assert created["scorer_label"] == "Character Error Rate (CER)"
    assert created["direction"] == "lower_is_better"
    assert created["scorer_params"] == {"language": "bo"}  # default filled in
    assert created["package_version"] == "0.2.0"
    assert created["repo_name"] == "challenge-tibetan-ocr-woodblocks"
    assert created["sync_status"] == "pending"
    assert created["sync_attempts"] == 0
    assert created["locked_at"] is None
    assert created["media_archive_key"] is None
    assert "ground_truth_key" not in created

    # Inputs are public; the Ground Truth is stored privately, byte for byte.
    assert created["inputs_key"].startswith("evalai/public/")
    assert storage.objects[created["inputs_key"]] == inputs
    assert storage.public_url(created["inputs_key"])
    [private_key] = [key for key in storage.objects if key.startswith("evalai/private/")]
    assert storage.objects[private_key] == ground_truth
    assert len(storage.objects) == 2

    # Creating never talks to GitHub; the Sync Worker publishes later.
    assert repo_host.repos == {}

    read_back = client.get(f"/challenges/{created['id']}").json()
    assert read_back == created


# One valid Challenge per registered Scorer: (Scorer, parameters sent,
# Inputs, Ground Truth, parameters stored, Direction).
VALID_DATA_PER_SCORER = [
    pytest.param(
        "cer", "{}",
        jsonl({"id": "p1", "input": "img"}),
        jsonl({"id": "p1", "reference": "བཀྲ་ཤིས་"}),
        {"language": "bo"}, "lower_is_better", id="cer",
    ),
    pytest.param(
        "wer", '{"language": "en"}',
        jsonl({"id": "a", "input": "clip"}),
        jsonl({"id": "a", "reference": "the cat sat"}),
        {"language": "en"}, "lower_is_better", id="wer",
    ),
    pytest.param(
        "span_f1", '{"iou_threshold": 0.75}',
        jsonl({"id": "s1", "input": "om mani padme hum"}),
        jsonl({"id": "s1", "reference": [{"start": 0, "end": 1, "tag": "mantra"}]}),
        {"iou_threshold": 0.75}, "higher_is_better", id="span_f1",
    ),
    pytest.param(
        "toc", "{}",
        jsonl({"id": "t1", "input": "chapter one text chapter two text"}),
        jsonl({"id": "t1", "reference": [{"depth": 1, "start": 0}, {"depth": 1, "start": 17}]}),
        {"tolerance": 3}, "higher_is_better", id="toc",
    ),
    pytest.param(
        "mcq_accuracy", "",
        jsonl({"id": "q1", "input": {"question": "?", "choices": ["A", "B"]}}),
        jsonl({"id": "q1", "reference": "B"}),
        {}, "higher_is_better", id="mcq_accuracy",
    ),
]


@pytest.mark.parametrize(
    "scorer_name, sent_params, inputs, ground_truth, stored_params, direction",
    VALID_DATA_PER_SCORER,
)
def test_admin_creates_a_challenge_with_each_registered_scorer(
    client, admin_headers, category, scorer_name, sent_params, inputs, ground_truth, stored_params, direction
):
    response = create_challenge(
        client,
        admin_headers,
        category,
        title=f"A {scorer_name} challenge",
        scorer_name=scorer_name,
        scorer_params=sent_params,
        inputs=inputs,
        ground_truth=ground_truth,
    )

    assert response.status_code == 201, response.text
    created = response.json()
    assert created["scorer_name"] == scorer_name
    assert created["scorer_params"] == stored_params
    assert created["direction"] == direction
    assert created["sync_status"] == "pending"


def assert_rejected(response, storage, client):
    """The create was refused with 422, and nothing was stored or saved."""
    assert response.status_code == 422, response.text
    assert storage.objects == {}
    assert client.get("/challenges/list").json() == []
    return response.json()["errors"]


def test_inputs_and_ground_truth_with_different_ids_are_rejected_naming_each_id(
    client, admin_headers, category, storage
):
    response = create_challenge(
        client,
        admin_headers,
        category,
        inputs=jsonl({"id": "1", "input": "x"}, {"id": "2", "input": "y"}),
        ground_truth=jsonl({"id": "1", "reference": "ཀ"}, {"id": "9", "reference": "ཁ"}),
    )

    errors = assert_rejected(response, storage, client)
    by_id = {error["id"]: error for error in errors}
    assert set(by_id) == {"2", "9"}
    assert by_id["2"]["field"] == "ground_truth_file"
    assert "no Reference" in by_id["2"]["message"]
    assert by_id["9"]["field"] == "ground_truth_file"
    assert "not in the Inputs" in by_id["9"]["message"]


def test_a_duplicated_id_is_rejected_naming_the_file_and_line(client, admin_headers, category, storage):
    response = create_challenge(
        client,
        admin_headers,
        category,
        inputs=jsonl({"id": "1", "input": "x"}, {"id": "2", "input": "y"}),
        ground_truth=jsonl(
            {"id": "1", "reference": "ཀ"}, {"id": "2", "reference": "ཁ"}, {"id": "2", "reference": "ག"}
        ),
    )

    [error] = assert_rejected(response, storage, client)
    assert error["field"] == "ground_truth_file"
    assert error["line"] == 3
    assert "duplicate id '2'" in error["message"]


def test_a_line_that_is_not_json_is_rejected_naming_the_line(client, admin_headers, category, storage):
    response = create_challenge(
        client,
        admin_headers,
        category,
        inputs=b'{"id": "1", "input": "x"}\n{"id": "2", "input": \n',
    )

    [error] = assert_rejected(response, storage, client)
    assert (error["field"], error["line"]) == ("inputs_file", 2)
    assert "invalid JSON" in error["message"]


def test_references_that_do_not_match_the_scorer_are_rejected_naming_each_id(
    client, admin_headers, category, storage
):
    response = create_challenge(
        client,
        admin_headers,
        category,
        scorer_name="span_f1",
        inputs=jsonl(
            {"id": "ok", "input": "om mani"},
            {"id": "no-tag", "input": "om mani"},
            {"id": "past-end", "input": "om"},
        ),
        ground_truth=jsonl(
            {"id": "ok", "reference": [{"start": 0, "end": 1, "tag": "m"}]},
            {"id": "no-tag", "reference": [{"start": 0, "end": 1}]},
            {"id": "past-end", "reference": [{"start": 0, "end": 40, "tag": "m"}]},
        ),
    )

    errors = assert_rejected(response, storage, client)
    assert {(e["field"], e["id"]) for e in errors} == {
        ("ground_truth_file", "no-tag"),
        ("ground_truth_file", "past-end"),
    }
    by_id = {e["id"]: e["message"] for e in errors}
    assert "tag" in by_id["no-tag"]
    assert "span" in by_id["past-end"]


def test_inputs_that_do_not_match_the_scorer_are_rejected_naming_the_id(
    client, admin_headers, category, storage
):
    response = create_challenge(
        client,
        admin_headers,
        category,
        scorer_name="toc",
        inputs=jsonl({"id": "t1", "input": {"text": "not a string"}}),
        ground_truth=jsonl({"id": "t1", "reference": []}),
    )

    [error] = assert_rejected(response, storage, client)
    assert (error["field"], error["id"]) == ("inputs_file", "t1")


@pytest.mark.parametrize(
    "scorer_name, scorer_params, field, expected_in_message",
    [
        pytest.param("span_f1", '{"iou_threshold": 1.5}', "scorer_params", "iou_threshold", id="iou-above-1"),
        pytest.param("span_f1", '{"iou_threshold": 0}', "scorer_params", "iou_threshold", id="iou-zero"),
        pytest.param("cer", '{"lang": "bo"}', "scorer_params", "lang", id="unknown-parameter"),
        pytest.param("cer", '{"language": ', "scorer_params", "JSON", id="params-not-json"),
        pytest.param("cer", '["bo"]', "scorer_params", "object", id="params-not-object"),
        pytest.param("bleu", "{}", "scorer_name", "bleu", id="unknown-scorer"),
    ],
)
def test_an_invalid_scorer_or_parameter_is_rejected(
    client, admin_headers, category, storage, scorer_name, scorer_params, field, expected_in_message
):
    response = create_challenge(
        client,
        admin_headers,
        category,
        scorer_name=scorer_name,
        scorer_params=scorer_params,
        inputs=jsonl({"id": "1", "input": "om"}),
        ground_truth=jsonl({"id": "1", "reference": []}),
    )

    [error] = assert_rejected(response, storage, client)
    assert error["field"] == field
    assert expected_in_message in error["message"]


def zip_of(*names):
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            archive.writestr(name, b"\x89PNG fake image " + name.encode())
    return buffer.getvalue()


OCR_INPUTS = jsonl(
    {"id": "p1", "input": "page 1", "media": "images/p1.png"},
    {"id": "p2", "input": "page 2", "media": "images/p2.png"},
)
OCR_GROUND_TRUTH = jsonl({"id": "p1", "reference": "ཀ"}, {"id": "p2", "reference": "ཁ"})


def test_admin_creates_a_challenge_with_a_media_archive_stored_publicly(
    client, admin_headers, category, storage
):
    archive = zip_of("images/p1.png", "images/p2.png", "images/unused.png")

    response = create_challenge(
        client,
        admin_headers,
        category,
        inputs=OCR_INPUTS,
        ground_truth=OCR_GROUND_TRUTH,
        media_archive=archive,
    )

    assert response.status_code == 201, response.text
    key = response.json()["media_archive_key"]
    assert key.startswith("evalai/public/")
    assert storage.objects[key] == archive
    assert storage.public_url(key)


def test_media_paths_missing_from_the_archive_are_rejected_naming_each_id(
    client, admin_headers, category, storage
):
    response = create_challenge(
        client,
        admin_headers,
        category,
        inputs=OCR_INPUTS,
        ground_truth=OCR_GROUND_TRUTH,
        media_archive=zip_of("images/p1.png"),
    )

    [error] = assert_rejected(response, storage, client)
    assert (error["field"], error["id"]) == ("inputs_file", "p2")
    assert "images/p2.png" in error["message"]


def test_media_paths_without_an_archive_are_rejected(client, admin_headers, category, storage):
    response = create_challenge(
        client, admin_headers, category, inputs=OCR_INPUTS, ground_truth=OCR_GROUND_TRUTH
    )

    errors = assert_rejected(response, storage, client)
    assert {(e["field"], e["id"]) for e in errors} == {("inputs_file", "p1"), ("inputs_file", "p2")}


def test_a_media_archive_that_is_not_a_zip_is_rejected(client, admin_headers, category, storage):
    response = create_challenge(
        client,
        admin_headers,
        category,
        inputs=OCR_INPUTS,
        ground_truth=OCR_GROUND_TRUTH,
        media_archive=b"not a zip at all",
    )

    errors = assert_rejected(response, storage, client)
    assert errors[0]["field"] == "media_archive_file"
    assert "zip" in errors[0]["message"]


def test_a_media_archive_over_the_size_limit_is_rejected(app, client, admin_headers, category, storage):
    from service.challenge_data import media_archive_size_limit

    # The real limit is 2 GB; the test lowers it rather than upload 2 GB.
    archive = zip_of("images/p1.png", "images/p2.png")
    app.dependency_overrides[media_archive_size_limit] = lambda: len(archive) - 1

    response = create_challenge(
        client,
        admin_headers,
        category,
        inputs=OCR_INPUTS,
        ground_truth=OCR_GROUND_TRUTH,
        media_archive=archive,
    )

    [error] = assert_rejected(response, storage, client)
    assert error["field"] == "media_archive_file"
    assert "larger than" in error["message"]



def test_a_title_that_duplicates_an_existing_challenge_is_rejected(
    client, admin_headers, category, storage
):
    first = create_challenge(client, admin_headers, category, title="Tibetan OCR")
    assert first.status_code == 201, first.text
    stored_before = dict(storage.objects)

    response = create_challenge(client, admin_headers, category, title="Tibetan OCR")

    assert response.status_code == 409, response.text
    assert "Tibetan OCR" in response.json()["detail"]
    assert storage.objects == stored_before
    assert [c["title"] for c in client.get("/challenges/list").json()] == ["Tibetan OCR"]


def test_a_title_whose_repo_name_is_taken_is_rejected(client, admin_headers, category, storage):
    first = create_challenge(client, admin_headers, category, title="Tibetan OCR")
    assert first.status_code == 201, first.text

    response = create_challenge(client, admin_headers, category, title="tibetan  ocr!")

    assert response.status_code == 409, response.text
    assert "challenge-tibetan-ocr" in response.json()["detail"]
    assert len(client.get("/challenges/list").json()) == 1


def test_a_title_that_cannot_name_a_repo_is_rejected(client, admin_headers, category, storage):
    response = create_challenge(client, admin_headers, category, title="བོད་ཡིག")

    [error] = assert_rejected(response, storage, client)
    assert error["field"] == "title"


def test_an_unknown_category_is_rejected(client, admin_headers, storage):
    import uuid
    from types import SimpleNamespace

    no_such_category = SimpleNamespace(id=uuid.uuid4())

    response = create_challenge(client, admin_headers, no_such_category)

    [error] = assert_rejected(response, storage, client)
    assert error["field"] == "category_id"
