"""Per-item scores never leave the API. With no hidden test split, a score
for every Input would let participants reconstruct the Ground Truth, so no
endpoint returns them to anyone: not to visitors, not to the submitter, not
to admins. Responses carry the Scorer's Public Summary instead.

Every endpoint that returns Submission scores belongs in ``ENDPOINTS``."""

import pytest

from test_create_challenge import category, create_challenge, jsonl  # noqa: F401  (category is a fixture)
from test_model_submission import participant, submit  # noqa: F401  (participant is a fixture)

# Input ids no response has any reason to contain: per-item scores are keyed by them.
INPUT_IDS = ("folio-alpha", "folio-beta")

# (who calls, method, path); ``{id}`` and ``{challenge_id}`` are filled in.
ENDPOINTS = [
    ("anonymous", "GET", "/submissions/{id}"),
    ("anonymous", "GET", "/submissions/{id}/status"),
    ("submitter", "GET", "/submissions/{id}"),
    ("submitter", "GET", "/submissions/{id}/status"),
    ("submitter", "GET", "/submissions/my"),
    ("admin", "GET", "/submissions/{id}"),
    ("admin", "GET", "/submissions/{id}/status"),
    ("admin", "GET", "/submissions"),
    ("admin", "GET", "/submissions?challenge_id={challenge_id}"),
    ("admin", "POST", "/submissions/{id}/resync"),
]


@pytest.fixture
def scored_submission(client, admin_headers, headers_for, participant, category, submission_worker):
    """A Submission scored on a CER Challenge whose Inputs are ``INPUT_IDS``,
    one Prediction right and one wrong."""
    challenge = create_challenge(
        client, admin_headers, category,
        inputs=jsonl(*({"id": input_id, "input": "x"} for input_id in INPUT_IDS)),
        ground_truth=jsonl(*({"id": input_id, "reference": "ཀཁ"} for input_id in INPUT_IDS)),
    ).json()
    predictions = jsonl({"id": INPUT_IDS[0], "prediction": "ཀཁ"}, {"id": INPUT_IDS[1], "prediction": "ཀ"})
    response = submit(client, headers_for(participant), challenge, predictions=predictions)
    assert response.status_code == 202, response.text
    submission_worker.drain()
    return {"id": response.json()["id"], "challenge_id": challenge["id"]}


@pytest.mark.parametrize("who, method, path", ENDPOINTS)
def test_no_endpoint_returns_per_item_scores(
    client, headers_for, participant, admin_headers, scored_submission, who, method, path
):
    """Whoever calls, a scored Submission's response holds no score details
    and names no Input."""
    headers = {"anonymous": {}, "submitter": headers_for(participant), "admin": admin_headers}[who]

    response = client.request(method, path.format(**scored_submission), headers=headers)

    assert response.status_code == 200, response.text
    assert scored_submission["id"] in response.text
    assert "score_details" not in response.text
    for input_id in INPUT_IDS:
        assert input_id not in response.text
