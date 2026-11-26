"""Prompt Submission: a participant submits the prompt they ran (typed or as a
file) with the exact Preferred Model id and a license, and it is checked,
stored and scored exactly like a Model Submission."""

import pytest

from test_create_challenge import category, jsonl  # noqa: F401  (category is a fixture)
from test_model_submission import assert_rejected, challenge, participant, read, submit  # noqa: F401  (fixtures)

PROMPT = "Transcribe the Tibetan text in the image. Answer with the text only.\n"


def submit_prompt(client, headers, challenge, *, prompt_file=None, predictions=None, **fields):
    """POST a Prompt Submission typed into the text area, or uploaded as
    ``prompt_file`` (bytes) when given; ``fields`` override the form fields."""
    predictions = (
        jsonl({"id": "1", "prediction": "ཀ"}, {"id": "2", "prediction": "ཁ"})
        if predictions is None
        else predictions
    )
    data = {
        "challenge_id": challenge["id"],
        "type": "prompt",
        "name": "Careful transcriber",
        "description": "System prompt; the image is the user message. Temperature 0.",
        "preferred_model": "gemini-2.5-pro",
        "license": "CC-BY-4.0",
        **({} if prompt_file is not None else {"prompt_text": PROMPT}),
        **fields,
    }
    files = {"predictions_file": ("predictions.jsonl", predictions, "application/jsonl")}
    if prompt_file is not None:
        files["prompt_file"] = ("prompt.md", prompt_file, "text/markdown")
    return client.post("/submissions/create-submission", data=data, files=files, headers=headers)


def test_a_typed_prompt_is_scored_end_to_end(client, headers_for, participant, challenge, submission_worker):
    """A Prompt typed into the text area is accepted, scored by the Challenge's
    Scorer, and read back with its Preferred Model and license."""
    response = submit_prompt(client, headers_for(participant), challenge)
    assert response.status_code == 202, response.text

    submission_worker.drain()

    submission = read(client, response.json()["id"])
    assert submission["status"] == "completed"
    assert submission["headline_metric"] == 0.0
    assert submission["type"] == "prompt"
    assert submission["preferred_model"] == "gemini-2.5-pro"
    assert submission["license"] == "CC-BY-4.0"


def test_an_uploaded_prompt_file_is_scored_end_to_end(
    client, headers_for, participant, challenge, submission_worker
):
    """A Prompt uploaded as a file is accepted and scored like a typed one."""
    response = submit_prompt(client, headers_for(participant), challenge, prompt_file=PROMPT.encode())
    assert response.status_code == 202, response.text

    submission_worker.drain()

    submission = read(client, response.json()["id"])
    assert (submission["status"], submission["headline_metric"]) == ("completed", 0.0)


def _new_prompt_object(storage, stored_before):
    from ports.object_storage import PRIVATE_PREFIX

    [key] = [k for k in set(storage.objects) - stored_before if not k.endswith("predictions.jsonl")]
    assert key.startswith(PRIVATE_PREFIX)
    return key


def test_a_typed_prompt_is_stored_privately_as_markdown(client, headers_for, participant, challenge, storage):
    """The typed prompt is kept in private object storage as a markdown file,
    exactly as typed, and its key is not in the public Submission read."""
    stored_before = set(storage.objects)
    typed = "## Task\n\nརྒྱ་གར་སྐད་དུ། Transcribe {image}.\n"

    submission_id = submit_prompt(client, headers_for(participant), challenge, prompt_text=typed).json()["id"]

    key = _new_prompt_object(storage, stored_before)
    assert key.endswith(".md")
    assert storage.content_types[key].startswith("text/markdown")
    assert storage.objects[key] == typed.encode()
    assert key not in str(read(client, submission_id))


def test_an_uploaded_prompt_file_is_stored_privately_as_markdown(
    client, headers_for, participant, challenge, storage
):
    """An uploaded prompt file is kept byte for byte as a markdown file,
    whatever the uploaded file was called."""
    stored_before = set(storage.objects)
    uploaded = "You are a careful OCR engine.\r\nKeep line breaks.".encode()

    submit_prompt(client, headers_for(participant), challenge, prompt_file=uploaded)

    key = _new_prompt_object(storage, stored_before)
    assert key.endswith(".md")
    assert storage.content_types[key].startswith("text/markdown")
    assert storage.objects[key] == uploaded


@pytest.mark.parametrize("blank", ["", "  \n\t "])
def test_a_missing_prompt_is_rejected(client, headers_for, participant, challenge, assert_rejected, blank):
    """A Prompt Submission needs a prompt; a blank text area does not count."""
    response = submit_prompt(client, headers_for(participant), challenge, prompt_text=blank)

    [error] = assert_rejected(response)
    assert error["field"] == "prompt_text"
    assert "required" in error["message"]


def test_an_empty_prompt_file_is_rejected(client, headers_for, participant, challenge, assert_rejected):
    """An empty uploaded prompt file is no prompt."""
    response = submit_prompt(client, headers_for(participant), challenge, prompt_file=b"")

    [error] = assert_rejected(response)
    assert error["field"] == "prompt_text"


def test_a_prompt_both_typed_and_uploaded_is_rejected(
    client, headers_for, participant, challenge, assert_rejected
):
    """The prompt comes typed or as a file, not both, so there is no doubt
    which one was run."""
    response = submit_prompt(
        client, headers_for(participant), challenge, prompt_file=PROMPT.encode(), prompt_text="Another prompt."
    )

    [error] = assert_rejected(response)
    assert error["field"] == "prompt_file"
    assert "not both" in error["message"]


def test_a_prompt_file_that_is_not_utf8_text_is_rejected(
    client, headers_for, participant, challenge, assert_rejected
):
    """A prompt file must be UTF-8 text, since it is kept and shown as markdown."""
    response = submit_prompt(client, headers_for(participant), challenge, prompt_file=b"\x89PNG\r\n\x1a\n\xff\xfe")

    [error] = assert_rejected(response)
    assert error["field"] == "prompt_file"
    assert "UTF-8" in error["message"]


@pytest.mark.parametrize("field", ["preferred_model", "license"])
def test_a_prompt_without_preferred_model_or_license_is_rejected(
    client, headers_for, participant, challenge, assert_rejected, field
):
    """Preferred Model and license are required on a Prompt; whitespace alone
    does not count."""
    response = submit_prompt(client, headers_for(participant), challenge, **{field: "  "})

    [error] = assert_rejected(response)
    assert (error["field"], error["message"]) == (field, "is required")


def test_a_preferred_model_the_arena_has_never_seen_is_accepted(
    client, headers_for, participant, challenge
):
    """Preferred Model is free text, so a model id released yesterday is fine."""
    response = submit_prompt(
        client, headers_for(participant), challenge, preferred_model="acme/brand-new-model-2026-11-25"
    )

    assert response.status_code == 202, response.text
    assert read(client, response.json()["id"])["preferred_model"] == "acme/brand-new-model-2026-11-25"


@pytest.mark.parametrize("license_id", ["CC-BY-4.0", "CC0-1.0", "MIT", "Apache-2.0"])
def test_each_offered_license_is_accepted(client, headers_for, participant, challenge, license_id):
    """A Prompt may carry any of the four offered licenses."""
    response = submit_prompt(client, headers_for(participant), challenge, license=license_id)

    assert response.status_code == 202, response.text
    assert read(client, response.json()["id"])["license"] == license_id


@pytest.mark.parametrize("license_id", ["GPL-3.0", "cc-by-4.0", "CC-BY-SA-4.0", "proprietary"])
def test_a_license_outside_the_four_offered_is_rejected(
    client, headers_for, participant, challenge, assert_rejected, license_id
):
    """Any license other than CC-BY-4.0, CC0-1.0, MIT or Apache-2.0 is refused,
    naming the choices."""
    response = submit_prompt(client, headers_for(participant), challenge, license=license_id)

    [error] = assert_rejected(response)
    assert error["field"] == "license"
    assert "CC-BY-4.0, CC0-1.0, MIT, Apache-2.0" in error["message"]


def test_a_prompt_submission_does_not_accept_a_model_link(
    client, headers_for, participant, challenge, assert_rejected
):
    """A model link belongs to Model Submissions; a Prompt names its model
    in Preferred Model instead."""
    response = submit_prompt(client, headers_for(participant), challenge, model_link="https://huggingface.co/x")

    [error] = assert_rejected(response)
    assert error["field"] == "model_link"
    assert "not accepted" in error["message"]


@pytest.mark.parametrize(
    "field, value",
    [
        ("preferred_model", "gemini-2.5-pro"),
        ("license", "MIT"),
        ("prompt_text", PROMPT),
    ],
)
def test_a_model_submission_does_not_accept_prompt_fields(
    client, headers_for, participant, challenge, assert_rejected, field, value
):
    """A Model Submission is not asked for, and refuses, a Preferred Model,
    a license or a prompt."""
    response = submit(client, headers_for(participant), challenge, **{field: value})

    [error] = assert_rejected(response)
    assert error["field"] == field
    assert "not accepted" in error["message"]


def test_a_model_submission_does_not_accept_a_prompt_file(
    client, headers_for, participant, challenge, assert_rejected
):
    """An uploaded prompt file is refused on a Model Submission too."""
    files = {
        "predictions_file": ("p.jsonl", jsonl({"id": "1", "prediction": "ཀ"}, {"id": "2", "prediction": "ཁ"})),
        "prompt_file": ("prompt.md", PROMPT.encode(), "text/markdown"),
    }
    data = {"challenge_id": challenge["id"], "type": "model", "name": "OCR", "description": "d"}

    response = client.post(
        "/submissions/create-submission", data=data, files=files, headers=headers_for(participant)
    )

    [error] = assert_rejected(response)
    assert error["field"] == "prompt_file"
    assert "not accepted" in error["message"]


def test_a_prompt_needs_no_input_placeholder(client, headers_for, participant, challenge):
    """The prompt's content is not checked: one without an ``{input}``
    placeholder is accepted."""
    response = submit_prompt(client, headers_for(participant), challenge, prompt_text="Just do OCR.")

    assert response.status_code == 202, response.text


def test_a_prompt_submission_creates_no_model(client, headers_for, participant, challenge):
    """A Prompt Submission is not a model: it neither creates nor references one."""
    submission_id = submit_prompt(client, headers_for(participant), challenge).json()["id"]

    assert read(client, submission_id)["model_id"] is None
    assert client.get("/model/").json() == []
