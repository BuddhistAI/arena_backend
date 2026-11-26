"""Leaderboard: a Challenge's scored Submissions of every Submission Type,
ranked on the server by Headline Metric in the Scorer's Direction, the earlier
Submission first on a tie. Anyone may read it, signed in or not."""

import datetime

import pytest

from test_create_challenge import category, create_challenge, jsonl  # noqa: F401  (category is a fixture)
from test_model_submission import (  # noqa: F401  (challenge, participant, span_challenge are fixtures)
    UNSCORABLE_SPANS,
    challenge,
    participant,
    span_challenge,
    submit,
)
from test_prompt_submission import submit_prompt
from test_skill_submission import submit_skill


def leaderboard(client, challenge, **params):
    """The Challenge's Leaderboard as an anonymous visitor reads it."""
    response = client.get(f"/challenges/{challenge['id']}/leaderboard", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_a_scored_submission_is_ranked_first_on_its_challenges_leaderboard(
    client, headers_for, participant, challenge, submission_worker
):
    """An anonymous visitor sees the one scored Submission as Rank 1."""
    submission_id = submit(client, headers_for(participant), challenge).json()["id"]
    submission_worker.drain()

    [row] = leaderboard(client, challenge)["rows"]

    assert (row["submission_id"], row["rank"]) == (submission_id, 1)


def test_an_unknown_challenge_has_no_leaderboard(client):
    """A Challenge id nobody created answers 404."""
    response = client.get("/challenges/00000000-0000-0000-0000-000000000000/leaderboard")

    assert response.status_code == 404, response.text


def test_a_deleted_challenge_has_no_leaderboard(client, admin_headers, challenge):
    """Once an admin deletes the Challenge its Leaderboard answers 404."""
    response = client.delete(f"/challenges/remove/{challenge['id']}", headers=admin_headers)
    assert response.status_code == 200, response.text

    response = client.get(f"/challenges/{challenge['id']}/leaderboard")

    assert response.status_code == 404, response.text


def ranked_ids(board):
    """The Leaderboard's submission ids with their Ranks, in the order returned."""
    return [(row["submission_id"], row["rank"]) for row in board["rows"]]


def submit_scored(client, headers, challenge, submission_worker, clock, submit_fn=submit, **fields):
    """Submit, score it, and move the clock on a minute so the next Submission
    is later. Returns the Submission's id."""
    response = submit_fn(client, headers, challenge, **fields)
    assert response.status_code == 202, response.text
    submission_worker.drain()
    clock.advance(datetime.timedelta(minutes=1))
    return response.json()["id"]


def test_a_lower_is_better_challenge_ranks_the_lowest_headline_metric_first(
    client, headers_for, participant, challenge, submission_worker, clock
):
    """On a CER Challenge the lowest CER is Rank 1, whatever the order of submitting."""
    headers = headers_for(participant)
    both_wrong = submit_scored(
        client, headers, challenge, submission_worker, clock,
        predictions=jsonl({"id": "1", "prediction": "ག"}, {"id": "2", "prediction": "ང"}),
    )
    exact = submit_scored(client, headers, challenge, submission_worker, clock)
    one_wrong = submit_scored(
        client, headers, challenge, submission_worker, clock,
        predictions=jsonl({"id": "1", "prediction": "ཀ"}, {"id": "2", "prediction": "ང"}),
    )

    board = leaderboard(client, challenge)

    assert ranked_ids(board) == [(exact, 1), (one_wrong, 2), (both_wrong, 3)]


def mcq_challenge(client, admin_headers, category):
    """A higher-is-better MCQ accuracy Challenge with questions ``1`` to ``4``,
    each answered ``A``."""
    response = create_challenge(
        client, admin_headers, category,
        title="Tibetan MCQ", scorer_name="mcq_accuracy",
        inputs=jsonl(*({"id": str(i), "input": f"question {i}"} for i in range(1, 5))),
        ground_truth=jsonl(*({"id": str(i), "reference": "A"} for i in range(1, 5))),
    )
    assert response.status_code == 201, response.text
    return response.json()


def answers(correct):
    """MCQ Predictions answering the first ``correct`` of the four questions right."""
    return jsonl(*({"id": str(i), "prediction": "A" if i <= correct else "B"} for i in range(1, 5)))


def test_a_higher_is_better_challenge_ranks_the_highest_headline_metric_first(
    client, admin_headers, headers_for, participant, category, submission_worker, clock
):
    """On an MCQ accuracy Challenge the highest accuracy is Rank 1."""
    mcq = mcq_challenge(client, admin_headers, category)
    headers = headers_for(participant)
    half = submit_scored(client, headers, mcq, submission_worker, clock, predictions=answers(2))
    all_right = submit_scored(client, headers, mcq, submission_worker, clock, predictions=answers(4))
    one = submit_scored(client, headers, mcq, submission_worker, clock, predictions=answers(1))

    board = leaderboard(client, mcq)

    assert ranked_ids(board) == [(all_right, 1), (half, 2), (one, 3)]
    assert [row["headline_metric"] for row in board["rows"]] == [1.0, 0.5, 0.25]


def test_rank_follows_the_unrounded_headline_metric(
    client, admin_headers, headers_for, participant, category, submission_worker, clock
):
    """Two CERs that both show as 0.02% are ranked by their real values: one
    wrong character in 3001 beats one in 3000, though it came later."""
    long_lines = create_challenge(
        client, admin_headers, category,
        title="Long lines", scorer_params='{"language": "en"}',
        ground_truth=jsonl({"id": "1", "reference": "a" * 3000}, {"id": "2", "reference": "b" * 3001}),
    ).json()
    headers = headers_for(participant)
    wrong_in_3000 = submit_scored(
        client, headers, long_lines, submission_worker, clock,
        predictions=jsonl({"id": "1", "prediction": "a" * 2999 + "z"}, {"id": "2", "prediction": "b" * 3001}),
    )
    wrong_in_3001 = submit_scored(
        client, headers, long_lines, submission_worker, clock,
        predictions=jsonl({"id": "1", "prediction": "a" * 3000}, {"id": "2", "prediction": "b" * 3000 + "z"}),
    )

    board = leaderboard(client, long_lines)

    assert ranked_ids(board) == [(wrong_in_3001, 1), (wrong_in_3000, 2)]


def test_a_tie_goes_to_the_earlier_submission_and_each_row_keeps_its_own_rank(
    client, headers_for, make_user, challenge, submission_worker, clock
):
    """Three exact Submissions tie on CER: they are ranked 1, 2, 3 in the order
    they were made, whoever made them."""
    first, second = make_user("user", name="first"), make_user("user", name="second")
    earliest = submit_scored(client, headers_for(second), challenge, submission_worker, clock)
    middle = submit_scored(client, headers_for(first), challenge, submission_worker, clock)
    latest = submit_scored(client, headers_for(second), challenge, submission_worker, clock)

    board = leaderboard(client, challenge)

    assert ranked_ids(board) == [(earliest, 1), (middle, 2), (latest, 3)]


def test_a_submission_waiting_to_be_scored_is_not_ranked(
    client, headers_for, participant, challenge, submission_worker, clock
):
    """A Submission still pending is left off until it is scored."""
    scored = submit_scored(client, headers_for(participant), challenge, submission_worker, clock)
    submit(client, headers_for(participant), challenge)

    assert ranked_ids(leaderboard(client, challenge)) == [(scored, 1)]


def test_a_submission_that_failed_scoring_is_not_ranked(
    client, headers_for, participant, span_challenge, submission_worker, clock
):
    """A Submission the Scorer could not score never reaches the Leaderboard."""
    scored = submit_scored(
        client, headers_for(participant), span_challenge, submission_worker, clock,
        predictions=jsonl({"id": "s1", "prediction": [{"start": 0, "end": 1, "tag": "m"}]}),
    )
    submit_scored(
        client, headers_for(participant), span_challenge, submission_worker, clock, predictions=UNSCORABLE_SPANS
    )

    assert ranked_ids(leaderboard(client, span_challenge)) == [(scored, 1)]


def test_a_deleted_submission_leaves_the_leaderboard_and_the_rest_move_up(
    client, headers_for, participant, challenge, submission_worker, clock
):
    """Deleting the Rank 1 Submission makes the next one Rank 1."""
    headers = headers_for(participant)
    best = submit_scored(client, headers, challenge, submission_worker, clock)
    runner_up = submit_scored(
        client, headers, challenge, submission_worker, clock,
        predictions=jsonl({"id": "1", "prediction": "ཀ"}, {"id": "2", "prediction": "ང"}),
    )
    assert client.delete(f"/submissions/{best}", headers=headers).status_code == 204

    assert ranked_ids(leaderboard(client, challenge)) == [(runner_up, 1)]


def test_a_model_row_shows_its_type_name_submitter_and_no_preferred_model_or_download(
    client, headers_for, participant, challenge, submission_worker, clock
):
    """A Model row names the model, credits the submitter's username, leaves
    Preferred Model blank and offers no download."""
    clock.set(datetime.datetime(2026, 11, 20, 8, 15))
    submit_scored(client, headers_for(participant), challenge, submission_worker, clock, name="Whisper large-v3")

    [row] = leaderboard(client, challenge)["rows"]

    assert row["type"] == "model"
    assert row["name"] == "Whisper large-v3"
    assert row["submitter"] == "participant"
    assert row["created_at"].startswith("2026-11-20T08:15")
    assert row["preferred_model"] is None
    assert row["has_download"] is False


@pytest.mark.parametrize("submit_fn, submission_type", [(submit_prompt, "prompt"), (submit_skill, "skill")])
def test_a_prompt_or_skill_row_shows_its_preferred_model_and_offers_a_download(
    client, headers_for, participant, challenge, submission_worker, clock, submit_fn, submission_type
):
    """A Prompt or Skill row carries its type, the model it ran on and a download."""
    submit_scored(
        client, headers_for(participant), challenge, submission_worker, clock,
        submit_fn=submit_fn, preferred_model="claude-opus-4-1",
    )

    [row] = leaderboard(client, challenge)["rows"]

    assert row["type"] == submission_type
    assert row["preferred_model"] == "claude-opus-4-1"
    assert row["has_download"] is True


def test_a_row_credits_the_submitters_current_username_and_never_their_email_or_id(
    client, db, headers_for, participant, challenge, submission_worker, clock
):
    """After a rename the row shows the new username; the user's email and
    internal id appear nowhere in the response."""
    submit_scored(client, headers_for(participant), challenge, submission_worker, clock)
    participant.username = "tenzin"  # renaming has no endpoint yet (ticket 08)
    db.commit()

    response = client.get(f"/challenges/{challenge['id']}/leaderboard")

    [row] = response.json()["rows"]
    assert row["submitter"] == "tenzin"
    assert participant.email not in response.text
    assert participant.id not in response.text


def test_the_leaderboard_names_its_scorer_and_how_to_show_its_numbers(client, challenge):
    """Even with nothing ranked yet, a CER Challenge's Leaderboard carries the
    Scorer's name, label, Direction, percent format and Public Summary declaration."""
    board = leaderboard(client, challenge)

    assert board["scorer"] == {
        "name": "cer",
        "label": "Character Error Rate (CER)",
        "direction": "lower_is_better",
        "format": {"style": "percent", "decimals": 2},
        "public_summary": [
            {"key": "corpus", "label": "Corpus CER", "format": {"style": "percent", "decimals": 2}}
        ],
    }
    assert board["rows"] == []


def test_a_higher_is_better_scorer_is_described_with_its_own_direction_and_format(
    client, admin_headers, category
):
    """An MCQ accuracy Challenge's Scorer block says higher is better and how
    its accuracy and counts are shown."""
    scorer = leaderboard(client, mcq_challenge(client, admin_headers, category))["scorer"]

    assert (scorer["name"], scorer["direction"]) == ("mcq_accuracy", "higher_is_better")
    assert scorer["format"] == {"style": "percent", "decimals": 1}
    assert [entry["key"] for entry in scorer["public_summary"]] == ["n_correct", "n_total"]


def ranks(board):
    return [row["rank"] for row in board["rows"]]


def test_a_page_holds_25_rows_by_default_and_the_next_page_continues_the_ranking(
    client, headers_for, make_user, challenge, submission_worker, clock
):
    """With 27 ranked Submissions the first page holds Ranks 1 to 25 and page 2
    holds 26 and 27."""
    admin = make_user("admin", name="prolific")  # admins have no daily limit
    for _ in range(27):
        submit_scored(client, headers_for(admin), challenge, submission_worker, clock)

    first = leaderboard(client, challenge)
    second = leaderboard(client, challenge, page=2)

    assert (first["page"], first["limit"], ranks(first)) == (1, 25, list(range(1, 26)))
    assert (second["page"], second["limit"], ranks(second)) == (2, 25, [26, 27])


def test_paging_never_changes_a_rank(client, headers_for, make_user, challenge, submission_worker, clock):
    """Page 2 of 2-row pages holds the Submissions ranked 3 and 4."""
    admin = make_user("admin", name="prolific")
    made = [submit_scored(client, headers_for(admin), challenge, submission_worker, clock) for _ in range(5)]

    board = leaderboard(client, challenge, page=2, limit=2)

    assert ranked_ids(board) == [(made[2], 3), (made[3], 4)]


def test_a_page_size_above_the_cap_is_cut_to_the_cap(client, challenge):
    """Asking for 1000 rows at once returns pages of at most 100."""
    assert leaderboard(client, challenge, limit=1000)["limit"] == 100


@pytest.mark.parametrize("params", [{"page": 0}, {"limit": 0}, {"page": "x"}])
def test_a_page_or_page_size_below_one_is_refused(client, challenge, params):
    """Page and page size count from 1; anything else answers 422."""
    response = client.get(f"/challenges/{challenge['id']}/leaderboard", params=params)

    assert response.status_code == 422, response.text



@pytest.fixture(name="mixed_board")
def mixed_board_fixture(client, headers_for, make_user, challenge, submission_worker, clock):
    """Five exact Submissions, so tied and ranked in the order made:
    1 Model, 2 Prompt, 3 Model, 4 Skill, 5 Prompt. Returns their ids by Rank."""
    admin = make_user("admin", name="prolific")  # admins have no daily limit
    headers = headers_for(admin)
    return [
        submit_scored(client, headers, challenge, submission_worker, clock, name="Google Vision OCR"),
        submit_scored(
            client, headers, challenge, submission_worker, clock,
            submit_fn=submit_prompt, name="Careful transcriber", preferred_model="claude-opus-4-1",
        ),
        submit_scored(client, headers, challenge, submission_worker, clock, name="Whisper large-v3"),
        submit_scored(
            client, headers, challenge, submission_worker, clock,
            submit_fn=submit_skill, name="Tibetan OCR skill", preferred_model="gpt-5",
        ),
        submit_scored(
            client, headers, challenge, submission_worker, clock,
            submit_fn=submit_prompt, name="Line by line", preferred_model="Claude-Sonnet-4-5",
        ),
    ]


@pytest.mark.parametrize(
    "submission_type, expected_ranks",
    [("model", [1, 3]), ("prompt", [2, 5]), ("skill", [4])],
)
def test_filtering_by_submission_type_hides_other_rows_without_changing_a_rank(
    client, challenge, mixed_board, submission_type, expected_ranks
):
    """The Prompt tab shows the Prompts ranked #2 and #5 overall as #2 and #5."""
    board = leaderboard(client, challenge, type=submission_type)

    assert ranked_ids(board) == [(mixed_board[rank - 1], rank) for rank in expected_ranks]


@pytest.mark.parametrize(
    "q, expected_ranks",
    [
        ("WHISPER", [3]),  # Submission name, any case
        ("claude", [2, 5]),  # Preferred Model, any case
        ("by l", [5]),  # a substring from mid-name
    ],
)
def test_searching_hides_rows_that_do_not_match_without_changing_a_rank(
    client, challenge, mixed_board, q, expected_ranks
):
    """Search is a case-insensitive substring match over the name and the
    Preferred Model; the rows found keep their overall Rank."""
    board = leaderboard(client, challenge, q=q)

    assert ranked_ids(board) == [(mixed_board[rank - 1], rank) for rank in expected_ranks]


def test_searching_finds_a_submitters_rows_by_their_username(
    client, headers_for, make_user, challenge, mixed_board, submission_worker, clock
):
    """Searching part of a username, in any case, finds that submitter's rows
    at their overall Rank."""
    tenzin = make_user("user", name="Tenzin")
    sixth = submit_scored(client, headers_for(tenzin), challenge, submission_worker, clock)

    assert ranked_ids(leaderboard(client, challenge, q="tENz")) == [(sixth, 6)]


def test_search_text_is_matched_literally(client, challenge, mixed_board):
    """A ``%`` or ``_`` in the search is a character to find, not a wildcard."""
    assert leaderboard(client, challenge, q="%")["rows"] == []
    assert leaderboard(client, challenge, q="_")["rows"] == []


def test_a_type_filter_and_a_search_together_show_rows_matching_both(client, challenge, mixed_board):
    """"OCR" names a Model (#1) and a Skill (#4); on the Skill tab it finds
    only the Skill, still #4."""
    board = leaderboard(client, challenge, type="skill", q="ocr")

    assert ranked_ids(board) == [(mixed_board[3], 4)]


def test_an_unknown_submission_type_is_refused(client, challenge):
    """Only model, prompt and skill are Submission Types; anything else answers 422."""
    response = client.get(f"/challenges/{challenge['id']}/leaderboard", params={"type": "dataset"})

    assert response.status_code == 422, response.text
