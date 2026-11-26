"""List Scorers: any signed-in user sees every Scorer buddhist-eval registers."""


def _scorers_by_name(response):
    assert response.status_code == 200, response.text
    return {scorer["name"]: scorer for scorer in response.json()}


def test_signed_in_user_sees_every_scorer_with_its_direction(client, user_headers):
    scorers = _scorers_by_name(client.get("/scorers", headers=user_headers))

    directions = {name: scorer["direction"] for name, scorer in scorers.items()}
    assert directions == {
        "cer": "lower_is_better",
        "wer": "lower_is_better",
        "span_f1": "higher_is_better",
        "toc": "higher_is_better",
        "mcq_accuracy": "higher_is_better",
    }


def test_each_scorer_lists_its_parameters_with_defaults(client, user_headers):
    scorers = _scorers_by_name(client.get("/scorers", headers=user_headers))

    cer = scorers["cer"]
    assert cer["label"] == "Character Error Rate (CER)"
    assert cer["parameter_schema"]["properties"]["language"]["default"] == "bo"
    iou = scorers["span_f1"]["parameter_schema"]["properties"]["iou_threshold"]
    assert (iou["default"], iou["exclusiveMinimum"], iou["maximum"]) == (0.5, 0.0, 1.0)
    assert scorers["toc"]["parameter_schema"]["properties"]["tolerance"]["default"] == 3
    assert scorers["mcq_accuracy"]["parameter_schema"]["properties"] == {}


def test_listing_scorers_requires_sign_in(client):
    response = client.get("/scorers")

    assert response.status_code in (401, 403)
