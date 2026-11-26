"""Model Submission end to end: a signed-in participant submits a Predictions
File for a Model, it is checked at once, stored, Locks the Challenge, and is
scored in the background by the Challenge's locked Scorer."""

import pytest

from test_create_challenge import category, create_challenge, jsonl  # noqa: F401  (category is a fixture)


@pytest.fixture
def participant(make_user):
    return make_user("user", name="participant")


@pytest.fixture
def challenge(client, admin_headers, category):
    """A CER Challenge with two Inputs, ``1`` and ``2``."""
    response = create_challenge(client, admin_headers, category)
    assert response.status_code == 201, response.text
    return response.json()


def submit(client, headers, challenge, *, predictions=None, **fields):
    """POST a Model Submission; ``fields`` override the form fields."""
    predictions = (
        jsonl({"id": "1", "prediction": "ཀ"}, {"id": "2", "prediction": "ཁ"})
        if predictions is None
        else predictions
    )
    data = {
        "challenge_id": challenge["id"],
        "type": "model",
        "name": "Google Vision OCR",
        "description": "Vision API, default settings.",
        **fields,
    }
    files = {"predictions_file": ("predictions.jsonl", predictions, "application/jsonl")}
    return client.post("/submissions/create-submission", data=data, files=files, headers=headers)


def test_a_valid_model_submission_is_accepted_as_pending(client, headers_for, participant, challenge):
    """A valid Model Submission answers 202 with its id and status ``pending``."""
    response = submit(client, headers_for(participant), challenge)

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "pending"
    assert client.get(f"/submissions/{body['id']}").json()["status"] == "pending"


def read(client, submission_id):
    response = client.get(f"/submissions/{submission_id}")
    assert response.status_code == 200, response.text
    return response.json()


def test_a_model_submission_is_scored_by_the_challenges_scorer(
    client, admin_headers, headers_for, participant, category, submission_worker
):
    """Once drained, the Submission is ``completed`` with the locked Scorer's
    unrounded Headline Metric and details, and its type, name and link."""
    created = create_challenge(
        client, admin_headers, category,
        scorer_name="wer", scorer_params='{"language": "en"}',
        inputs=jsonl({"id": "a", "input": "clip a"}, {"id": "b", "input": "clip b"}),
        ground_truth=jsonl({"id": "a", "reference": "the cat sat"}, {"id": "b", "reference": "a dog"}),
    ).json()
    # Item a is exact (WER 0); item b gets one of its two words wrong (WER 1/2).
    predictions = jsonl({"id": "a", "prediction": "the cat sat"}, {"id": "b", "prediction": "a cat"})

    response = submit(
        client, headers_for(participant), created,
        predictions=predictions, name="Whisper large-v3", model_link="https://huggingface.co/openai/whisper-large-v3",
    )
    submission_worker.drain()

    submission = read(client, response.json()["id"])
    assert submission["status"] == "completed"
    assert submission["headline_metric"] == 0.25
    assert submission["score_details"]["items"]["b"]["rate"] == 0.5
    assert submission["type"] == "model"
    assert submission["name"] == "Whisper large-v3"
    assert submission["model_link"] == "https://huggingface.co/openai/whisper-large-v3"
    assert submission["status_message"] is None


@pytest.fixture
def assert_rejected(client, headers_for, participant, challenge, storage):
    """``assert_rejected(response)`` checks the Submission was refused with
    422 and left no trace: no object stored, no Submission, no new model, and
    the Challenge not Locked. It returns the errors."""
    stored_before = dict(storage.objects)
    models_before = client.get("/model/").json()

    def _assert_rejected(response):
        assert response.status_code == 422, response.text
        assert storage.objects == stored_before
        assert client.get("/submissions/my", headers=headers_for(participant)).json() == []
        assert client.get("/model/").json() == models_before
        assert client.get(f"/challenges/{challenge['id']}").json()["locked"] is False
        return response.json()["errors"]

    return _assert_rejected


def test_a_missing_input_id_is_rejected_naming_the_id(
    client, headers_for, participant, challenge, assert_rejected
):
    """A Predictions File without a row for an Input id is refused, naming it."""
    response = submit(
        client, headers_for(participant), challenge, predictions=jsonl({"id": "1", "prediction": "ཀ"})
    )

    [error] = assert_rejected(response)
    assert (error["field"], error["id"]) == ("predictions_file", "2")
    assert "no Prediction" in error["message"]


def test_an_unknown_id_is_rejected_naming_the_id(client, headers_for, participant, challenge, assert_rejected):
    """A row whose id is not an Input id is refused, naming it."""
    predictions = jsonl(
        {"id": "1", "prediction": "ཀ"}, {"id": "2", "prediction": "ཁ"}, {"id": "99", "prediction": "ག"}
    )

    response = submit(client, headers_for(participant), challenge, predictions=predictions)

    [error] = assert_rejected(response)
    assert (error["field"], error["id"]) == ("predictions_file", "99")
    assert "not an Input id" in error["message"]


def test_a_duplicated_id_is_rejected_naming_the_line(client, headers_for, participant, challenge, assert_rejected):
    """A second row for the same id is refused with its line number."""
    predictions = jsonl(
        {"id": "1", "prediction": "ཀ"}, {"id": "2", "prediction": "ཁ"}, {"id": "1", "prediction": "ག"}
    )

    response = submit(client, headers_for(participant), challenge, predictions=predictions)

    [error] = assert_rejected(response)
    assert (error["field"], error["line"]) == ("predictions_file", 3)
    assert "duplicate id '1'" in error["message"]


def test_a_line_that_is_not_json_is_rejected_naming_the_line(
    client, headers_for, participant, challenge, assert_rejected
):
    """A row that is not valid JSON is refused with its line number."""
    predictions = b'{"id": "1", "prediction": "x"}\n{"id": "2", "prediction": \n'

    response = submit(client, headers_for(participant), challenge, predictions=predictions)

    [error] = assert_rejected(response)
    assert (error["field"], error["line"]) == ("predictions_file", 2)
    assert "invalid JSON" in error["message"]


def test_an_empty_predictions_file_is_rejected(client, headers_for, participant, challenge, assert_rejected):
    """A Predictions File with no rows is refused."""
    response = submit(client, headers_for(participant), challenge, predictions=b"")

    [error] = assert_rejected(response)
    assert error["field"] == "predictions_file"
    assert "no rows" in error["message"]


def test_predictions_that_do_not_match_the_scorer_are_rejected_naming_each_id(
    client, admin_headers, headers_for, participant, category, storage
):
    """Every Prediction that does not match the Scorer's Prediction shape is
    reported with its id, and nothing is stored."""
    created = create_challenge(
        client, admin_headers, category,
        scorer_name="span_f1",
        inputs=jsonl({"id": "ok", "input": "om mani"}, {"id": "no-tag", "input": "om"}, {"id": "text", "input": "om"}),
        ground_truth=jsonl(
            {"id": "ok", "reference": []}, {"id": "no-tag", "reference": []}, {"id": "text", "reference": []}
        ),
    ).json()
    stored_before = dict(storage.objects)
    predictions = jsonl(
        {"id": "ok", "prediction": [{"start": 0, "end": 1, "tag": "m"}]},
        {"id": "no-tag", "prediction": [{"start": 0, "end": 1}]},
        {"id": "text", "prediction": "om"},
    )

    response = submit(client, headers_for(participant), created, predictions=predictions)

    assert response.status_code == 422, response.text
    errors = response.json()["errors"]
    assert {(e["field"], e["id"]) for e in errors} == {("predictions_file", "no-tag"), ("predictions_file", "text")}
    assert storage.objects == stored_before


@pytest.mark.parametrize(
    "link",
    [
        pytest.param("http://huggingface.co/x", id="http"),
        pytest.param("ftp://models.test/x", id="ftp"),
        pytest.param("javascript:alert(1)", id="javascript"),
        pytest.param("huggingface.co/x", id="no-scheme"),
        pytest.param("https://", id="no-host"),
    ],
)
def test_a_model_link_that_is_not_https_is_rejected(
    client, headers_for, participant, challenge, assert_rejected, link
):
    """A model link must be an ``https`` URL with a host."""
    response = submit(client, headers_for(participant), challenge, model_link=link)

    [error] = assert_rejected(response)
    assert error["field"] == "model_link"
    assert "https" in error["message"]


@pytest.mark.parametrize("field", ["name", "description"])
def test_a_blank_required_field_is_rejected(client, headers_for, participant, challenge, assert_rejected, field):
    """Name and description are required; whitespace alone does not count."""
    response = submit(client, headers_for(participant), challenge, **{field: "   "})

    [error] = assert_rejected(response)
    assert (error["field"], error["message"]) == (field, "is required")


def test_every_problem_is_reported_at_once(client, headers_for, participant, challenge, assert_rejected):
    """A Submission with several problems lists all of them in one answer."""
    response = submit(
        client, headers_for(participant), challenge,
        name="", model_link="http://x.test", predictions=jsonl({"id": "1", "prediction": "ཀ"}),
    )

    errors = assert_rejected(response)
    assert {e["field"] for e in errors} == {"name", "model_link", "predictions_file"}


@pytest.mark.parametrize("submission_type", ["prompt", "skill"])
def test_prompt_and_skill_submissions_are_not_accepted_yet(
    client, headers_for, participant, challenge, assert_rejected, submission_type
):
    """Only Model Submissions are accepted for now."""
    response = submit(client, headers_for(participant), challenge, type=submission_type)

    [error] = assert_rejected(response)
    assert error["field"] == "type"
    assert "not accepted yet" in error["message"]


def test_an_unknown_submission_type_is_rejected(client, headers_for, participant, challenge, assert_rejected):
    """The Submission Type must be model, prompt or skill."""
    response = submit(client, headers_for(participant), challenge, type="dataset")

    [error] = assert_rejected(response)
    assert error["field"] == "type"
    assert "model, prompt, skill" in error["message"]


def test_an_anonymous_visitor_cannot_submit(client, challenge, storage):
    """Submitting needs a signed-in user; nothing is stored for a visitor."""
    stored_before = dict(storage.objects)

    response = submit(client, {}, challenge)

    assert response.status_code == 403, response.text
    assert storage.objects == stored_before


def test_submitting_to_a_deleted_challenge_is_not_found(client, admin_headers, headers_for, participant, challenge):
    """A deleted Challenge takes no Submissions."""
    client.delete(f"/challenges/remove/{challenge['id']}", headers=admin_headers)

    response = submit(client, headers_for(participant), challenge)

    assert response.status_code == 404, response.text


def test_the_predictions_file_is_stored_privately_byte_for_byte(
    client, headers_for, participant, challenge, storage
):
    """An accepted Predictions File is kept in object storage under the
    private prefix, and its key is not in the public Submission read."""
    from ports.object_storage import PRIVATE_PREFIX

    stored_before = set(storage.objects)
    predictions = jsonl({"id": "1", "prediction": "ཀ"}, {"id": "2", "prediction": "ཁ"})

    submission_id = submit(client, headers_for(participant), challenge, predictions=predictions).json()["id"]

    [key] = set(storage.objects) - stored_before
    assert key.startswith(PRIVATE_PREFIX)
    assert storage.objects[key] == predictions
    assert key not in str(read(client, submission_id))


def test_the_first_stored_submission_locks_the_challenge(client, headers_for, participant, challenge, clock):
    """Storing the Challenge's first Submission Locks it at once, before scoring."""
    submit(client, headers_for(participant), challenge)

    locked = client.get(f"/challenges/{challenge['id']}").json()
    assert locked["locked"] is True
    assert locked["locked_at"] == clock().isoformat()


def test_a_later_submission_keeps_the_time_of_the_lock(client, headers_for, participant, challenge, clock):
    """The Lock happens once: later Submissions do not move ``locked_at``."""
    import datetime

    first_at = clock().isoformat()
    submit(client, headers_for(participant), challenge)
    clock.advance(datetime.timedelta(hours=1))
    submit(client, headers_for(participant), challenge)

    assert client.get(f"/challenges/{challenge['id']}").json()["locked_at"] == first_at


@pytest.fixture
def span_challenge(client, admin_headers, category):
    """A span F1 Challenge whose only Input text is two characters long."""
    response = create_challenge(
        client, admin_headers, category,
        scorer_name="span_f1",
        inputs=jsonl({"id": "s1", "input": "om"}),
        ground_truth=jsonl({"id": "s1", "reference": [{"start": 0, "end": 1, "tag": "m"}]}),
    )
    assert response.status_code == 201, response.text
    return response.json()


# Well shaped, so intake accepts it, but the span runs past the two-character
# text, so the Scorer raises while scoring.
UNSCORABLE_SPANS = jsonl({"id": "s1", "prediction": [{"start": 0, "end": 40, "tag": "m"}]})


def test_a_scorer_error_fails_the_submission_with_the_message(
    client, headers_for, participant, span_challenge, submission_worker
):
    """When the Scorer raises, the Submission is ``failed`` with the Scorer's
    message and has no Headline Metric, so it is never ranked."""
    response = submit(client, headers_for(participant), span_challenge, predictions=UNSCORABLE_SPANS)
    assert response.status_code == 202, response.text

    submission_worker.drain()

    submission = read(client, response.json()["id"])
    assert submission["status"] == "failed"
    assert "invalid span" in submission["status_message"]
    assert submission["headline_metric"] is None
    assert submission["score_details"] is None


def test_a_submission_that_fails_scoring_still_locks_the_challenge(
    client, headers_for, participant, span_challenge, submission_worker
):
    """The Lock comes from storing the Submission, so it stays when scoring fails."""
    submit(client, headers_for(participant), span_challenge, predictions=UNSCORABLE_SPANS)
    submission_worker.drain()

    assert client.get(f"/challenges/{span_challenge['id']}").json()["locked"] is True


def test_model_submissions_with_the_same_name_share_one_model(
    client, headers_for, participant, make_user, challenge
):
    """A Model Submission reuses the model of its name, whoever submits it,
    and a new name creates a new model."""
    other = make_user("user", name="other")

    first = read(client, submit(client, headers_for(participant), challenge, name="Google Vision OCR").json()["id"])
    again = read(client, submit(client, headers_for(other), challenge, name="Google Vision OCR").json()["id"])
    new = read(client, submit(client, headers_for(participant), challenge, name="Tesseract 5").json()["id"])

    assert first["model_id"] == again["model_id"]
    assert new["model_id"] != first["model_id"]
    assert sorted(m["name"] for m in client.get("/model/").json()) == ["Google Vision OCR", "Tesseract 5"]


def test_the_status_endpoint_reports_the_score_once_completed(
    client, headers_for, participant, challenge, submission_worker
):
    """Polling ``/status`` shows ``pending`` until scored, then the Headline Metric."""
    submission_id = submit(client, headers_for(participant), challenge).json()["id"]
    before = client.get(f"/submissions/{submission_id}/status").json()

    submission_worker.drain()
    after = client.get(f"/submissions/{submission_id}/status").json()

    assert (before["status"], before["headline_metric"]) == ("pending", None)
    assert (after["status"], after["headline_metric"]) == ("completed", 0.0)
