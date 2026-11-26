"""Skill Submission: a participant submits their skill as a zip with the exact
Preferred Model id and a license. The zip is published to a public repo
later, so it only gets safety checks (size, entry count, paths, symlinks,
file types); any layout is accepted, and it is scored like any Submission."""

import io
import os
import stat
import struct
import zipfile

import pytest

from test_create_challenge import category, jsonl  # noqa: F401  (category is a fixture)
from test_model_submission import assert_rejected, challenge, participant, read  # noqa: F401  (fixtures)


def make_zip(*entries):
    """A zip built in memory from ``(name, content)`` pairs or ready-made
    ``ZipInfo``/content pairs."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return buffer.getvalue()


SKILL_ZIP = make_zip(
    ("tibetan-ocr/SKILL.md", "# Tibetan OCR\n\nRun `python run.py <image>`.\n"),
    ("tibetan-ocr/run.py", "print('ཀ')\n"),
)


def submit_skill(client, headers, challenge, *, skill_zip=SKILL_ZIP, predictions=None, **fields):
    """POST a Skill Submission with ``skill_zip`` (bytes, or None for no
    upload); ``fields`` override the form fields."""
    predictions = (
        jsonl({"id": "1", "prediction": "ཀ"}, {"id": "2", "prediction": "ཁ"})
        if predictions is None
        else predictions
    )
    data = {
        "challenge_id": challenge["id"],
        "type": "skill",
        "name": "Tibetan OCR skill",
        "description": "Load the skill in Claude Code and run it on each image. Temperature 0.",
        "preferred_model": "claude-sonnet-4-5",
        "license": "MIT",
        **fields,
    }
    files = {"predictions_file": ("predictions.jsonl", predictions, "application/jsonl")}
    if skill_zip is not None:
        files["skill_file"] = ("skill.zip", skill_zip, "application/zip")
    return client.post("/submissions/create-submission", data=data, files=files, headers=headers)


def test_a_skill_is_scored_end_to_end(client, headers_for, participant, challenge, submission_worker):
    """A Skill zip is accepted, scored by the Challenge's Scorer, and read back
    with its Preferred Model and license."""
    response = submit_skill(client, headers_for(participant), challenge)
    assert response.status_code == 202, response.text

    submission_worker.drain()

    submission = read(client, response.json()["id"])
    assert (submission["status"], submission["headline_metric"]) == ("completed", 0.0)
    assert submission["type"] == "skill"
    assert submission["preferred_model"] == "claude-sonnet-4-5"
    assert submission["license"] == "MIT"
    assert submission["model_id"] is None


def test_a_skill_zip_is_stored_privately_as_uploaded(client, headers_for, participant, challenge, storage):
    """The zip is kept byte for byte in private object storage, not extracted,
    and its key is not in the public Submission read."""
    from ports.object_storage import PRIVATE_PREFIX

    stored_before = set(storage.objects)

    submission_id = submit_skill(client, headers_for(participant), challenge).json()["id"]

    [key] = [k for k in set(storage.objects) - stored_before if not k.endswith("predictions.jsonl")]
    assert key.startswith(PRIVATE_PREFIX)
    assert storage.objects[key] == SKILL_ZIP
    assert storage.content_types[key] == "application/zip"
    assert key not in str(read(client, submission_id))


def test_a_skill_zip_with_any_layout_and_no_skill_md_is_scored(
    client, headers_for, participant, challenge, submission_worker
):
    """There are no structure checks: a zip without SKILL.md, with files at
    the top level and nested folders, is accepted and scored."""
    skill_zip = make_zip(
        ("prompt.txt", "Transcribe the image."),
        ("scripts/deep/nested/ocr.sh", "#!/bin/sh\necho ཀ\n"),
        ("config/settings.yaml", "temperature: 0\n"),
        ("data/examples.jsonl", '{"id": "x"}\n'),
        ("empty-folder/", ""),
    )

    response = submit_skill(client, headers_for(participant), challenge, skill_zip=skill_zip)
    assert response.status_code == 202, response.text
    submission_worker.drain()

    assert read(client, response.json()["id"])["status"] == "completed"


@pytest.mark.parametrize("skill_zip", [None, b""])
def test_a_skill_without_a_zip_is_rejected(client, headers_for, participant, challenge, assert_rejected, skill_zip):
    """A Skill Submission needs its zip; an empty upload does not count."""
    response = submit_skill(client, headers_for(participant), challenge, skill_zip=skill_zip)

    [error] = assert_rejected(response)
    assert (error["field"], error["message"]) == ("skill_file", "is required")


@pytest.mark.parametrize(
    "name",
    ["skill/bin/ocr.exe", "skill/model.bin", "skill/vendored.zip", "skill/README", "skill/logo.png"],
)
def test_a_file_type_outside_the_allowlist_is_rejected_naming_it(
    client, headers_for, participant, challenge, assert_rejected, name
):
    """Only text files (.md .txt .py .sh .js .ts .json .jsonl .yaml .yml .csv)
    may be in a skill, so no binaries or nested archives reach the public
    repo; the rejection names the file and the rule."""
    skill_zip = make_zip(("skill/SKILL.md", "# OCR"), (name, b"\x00\x01"))

    response = submit_skill(client, headers_for(participant), challenge, skill_zip=skill_zip)

    [error] = assert_rejected(response)
    assert error["field"] == "skill_file"
    assert name in error["message"]
    assert "file type" in error["message"]
    assert ".md .txt .py .sh .js .ts .json .jsonl .yaml .yml .csv" in error["message"]


def test_a_symlink_is_rejected_naming_it(client, headers_for, participant, challenge, assert_rejected):
    """A symlink could point outside the skill's folder once extracted, so any
    symlink entry is refused, even one with an allowed extension."""
    link = zipfile.ZipInfo("skill/notes.md")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    skill_zip = make_zip(("skill/SKILL.md", "# OCR"), (link, "/etc/passwd"))

    response = submit_skill(client, headers_for(participant), challenge, skill_zip=skill_zip)

    [error] = assert_rejected(response)
    assert error["field"] == "skill_file"
    assert "skill/notes.md" in error["message"]
    assert "symlink" in error["message"]


@pytest.mark.parametrize("name", ["/etc/cron.d/evil.sh", "\\Windows\\evil.txt", "C:/Users/evil.txt"])
def test_an_absolute_path_is_rejected_naming_it(
    client, headers_for, participant, challenge, assert_rejected, name
):
    """An absolute path would extract outside the skill's folder."""
    skill_zip = make_zip(("skill/SKILL.md", "# OCR"), (name, "x"))

    response = submit_skill(client, headers_for(participant), challenge, skill_zip=skill_zip)

    [error] = assert_rejected(response)
    assert name in error["message"]
    assert "absolute path" in error["message"]


@pytest.mark.parametrize("name", ["../evil.sh", "skill/../../evil.md", "skill\\..\\..\\evil.md"])
def test_a_dot_dot_path_is_rejected_naming_it(client, headers_for, participant, challenge, assert_rejected, name):
    """A path climbing out with ``..`` would extract outside the skill's folder."""
    skill_zip = make_zip(("skill/SKILL.md", "# OCR"), (name, "x"))

    response = submit_skill(client, headers_for(participant), challenge, skill_zip=skill_zip)

    [error] = assert_rejected(response)
    assert name in error["message"]
    assert "'..' paths" in error["message"]


def test_a_zip_with_more_than_200_entries_is_rejected(client, headers_for, participant, challenge, assert_rejected):
    """A skill may hold at most 200 entries."""
    skill_zip = make_zip(*((f"skill/{i}.md", "x") for i in range(201)))

    response = submit_skill(client, headers_for(participant), challenge, skill_zip=skill_zip)

    [error] = assert_rejected(response)
    assert error["field"] == "skill_file"
    assert "201 entries" in error["message"]
    assert "limit is 200" in error["message"]


def test_a_zip_with_exactly_200_entries_is_accepted(client, headers_for, participant, challenge):
    """200 entries is within the limit."""
    skill_zip = make_zip(*((f"skill/{i}.md", "x") for i in range(200)))

    response = submit_skill(client, headers_for(participant), challenge, skill_zip=skill_zip)

    assert response.status_code == 202, response.text


def test_a_zip_over_10_mb_is_rejected(client, headers_for, participant, challenge, assert_rejected):
    """The uploaded zip itself may be at most 10 MB."""
    skill_zip = make_zip(("skill/data.txt", os.urandom(10 * 1024 * 1024)))

    response = submit_skill(client, headers_for(participant), challenge, skill_zip=skill_zip)

    [error] = assert_rejected(response)
    assert error["field"] == "skill_file"
    assert "limit is 10 MB" in error["message"]


def test_a_zip_extracting_to_over_25_mb_is_rejected(client, headers_for, participant, challenge, assert_rejected):
    """A small zip that extracts to more than 25 MB in total is refused."""
    skill_zip = make_zip(
        ("skill/a.txt", b"\0" * (13 * 1024 * 1024)), ("skill/b.txt", b"\0" * (13 * 1024 * 1024))
    )
    assert len(skill_zip) < 1024 * 1024

    response = submit_skill(client, headers_for(participant), challenge, skill_zip=skill_zip)

    [error] = assert_rejected(response)
    assert "extracts to 26 MB" in error["message"]
    assert "limit is 25 MB" in error["message"]


def _understate_size(skill_zip, name, declared):
    """The zip with ``name``'s uncompressed size rewritten to ``declared`` in
    both its local header and the central directory."""
    data = bytearray(skill_zip)
    with zipfile.ZipFile(io.BytesIO(skill_zip)) as archive:
        info = archive.getinfo(name)
    struct.pack_into("<I", data, info.header_offset + 22, declared)
    central = data.rfind(b"PK\x01\x02")
    struct.pack_into("<I", data, central + 24, declared)
    return bytes(data)


def test_a_zip_whose_header_understates_its_size_is_rejected(
    client, headers_for, participant, challenge, assert_rejected
):
    """The sizes the zip declares are not trusted: an entry that decompresses
    to more than it declares is refused, naming it."""
    honest = make_zip(("skill/bomb.txt", b"\0" * (26 * 1024 * 1024)))
    skill_zip = _understate_size(honest, "skill/bomb.txt", 10)

    response = submit_skill(client, headers_for(participant), challenge, skill_zip=skill_zip)

    [error] = assert_rejected(response)
    assert "skill/bomb.txt" in error["message"]


def test_a_file_that_is_not_a_zip_is_rejected(client, headers_for, participant, challenge, assert_rejected):
    """The Skill upload must be a zip archive."""
    response = submit_skill(client, headers_for(participant), challenge, skill_zip=b"# Just a markdown file\n")

    [error] = assert_rejected(response)
    assert (error["field"], error["message"]) == ("skill_file", "is not a valid zip file")


def test_every_unsafe_entry_is_reported_at_once(client, headers_for, participant, challenge, assert_rejected):
    """Each unsafe entry gets its own error, so the participant can fix them all."""
    skill_zip = make_zip(("skill/SKILL.md", "# OCR"), ("skill/tool.exe", b"MZ"), ("../escape.md", "x"))

    response = submit_skill(client, headers_for(participant), challenge, skill_zip=skill_zip)

    errors = assert_rejected(response)
    assert [("skill/tool.exe" in e["message"], "../escape.md" in e["message"]) for e in errors] == [
        (True, False),
        (False, True),
    ]


@pytest.mark.parametrize("field", ["preferred_model", "license"])
def test_a_skill_without_preferred_model_or_license_is_rejected(
    client, headers_for, participant, challenge, assert_rejected, field
):
    """Preferred Model and license are required on a Skill, as on a Prompt."""
    response = submit_skill(client, headers_for(participant), challenge, **{field: " "})

    [error] = assert_rejected(response)
    assert (error["field"], error["message"]) == (field, "is required")


def test_a_skill_license_outside_the_four_offered_is_rejected(
    client, headers_for, participant, challenge, assert_rejected
):
    """A Skill's license must be one of the four offered."""
    response = submit_skill(client, headers_for(participant), challenge, license="GPL-3.0")

    [error] = assert_rejected(response)
    assert error["field"] == "license"


@pytest.mark.parametrize(
    "field, value", [("model_link", "https://huggingface.co/x"), ("prompt_text", "Transcribe.")]
)
def test_a_skill_submission_does_not_accept_model_or_prompt_fields(
    client, headers_for, participant, challenge, assert_rejected, field, value
):
    """A Skill carries its zip only: a model link or a typed prompt is refused."""
    response = submit_skill(client, headers_for(participant), challenge, **{field: value})

    [error] = assert_rejected(response)
    assert error["field"] == field
    assert "not accepted for Skill" in error["message"]


def test_a_skill_submission_does_not_accept_a_prompt_file(
    client, headers_for, participant, challenge, assert_rejected
):
    """An uploaded prompt file is refused on a Skill Submission."""
    data = {
        "challenge_id": challenge["id"], "type": "skill", "name": "OCR", "description": "d",
        "preferred_model": "claude-sonnet-4-5", "license": "MIT",
    }
    files = {
        "predictions_file": ("p.jsonl", jsonl({"id": "1", "prediction": "ཀ"}, {"id": "2", "prediction": "ཁ"})),
        "skill_file": ("skill.zip", SKILL_ZIP, "application/zip"),
        "prompt_file": ("prompt.md", b"Transcribe.", "text/markdown"),
    }

    response = client.post("/submissions/create-submission", data=data, files=files, headers=headers_for(participant))

    [error] = assert_rejected(response)
    assert error["field"] == "prompt_file"


@pytest.mark.parametrize("submission_type", ["model", "prompt"])
def test_other_submission_types_do_not_accept_a_skill_zip(
    client, headers_for, participant, challenge, assert_rejected, submission_type
):
    """A skill zip belongs to Skill Submissions only."""
    data = {
        "challenge_id": challenge["id"], "type": submission_type, "name": "OCR", "description": "d",
        **(
            {"preferred_model": "gemini-2.5-pro", "license": "MIT", "prompt_text": "Transcribe."}
            if submission_type == "prompt"
            else {}
        ),
    }
    files = {
        "predictions_file": ("p.jsonl", jsonl({"id": "1", "prediction": "ཀ"}, {"id": "2", "prediction": "ཁ"})),
        "skill_file": ("skill.zip", SKILL_ZIP, "application/zip"),
    }

    response = client.post("/submissions/create-submission", data=data, files=files, headers=headers_for(participant))

    [error] = assert_rejected(response)
    assert error["field"] == "skill_file"
    assert "not accepted" in error["message"]
