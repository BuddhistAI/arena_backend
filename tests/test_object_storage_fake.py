"""The in-memory object storage honours the object storage port's contract."""

import io

import pytest

from ports.object_storage import ObjectNotFound, PrivateObjectError, private_key, public_key


def test_stored_bytes_come_back_unchanged(storage):
    key = public_key("challenges", "c1", "inputs.jsonl")
    storage.put(key, b'{"id": "1"}\n', content_type="application/jsonl")

    assert storage.get(key) == b'{"id": "1"}\n'


def test_file_like_bodies_are_stored_as_their_bytes(storage):
    key = public_key("challenges", "c1", "media.zip")
    storage.put(key, io.BytesIO(b"PK\x03\x04"))

    assert storage.get(key) == b"PK\x03\x04"


def test_public_and_private_material_live_under_separate_prefixes():
    assert public_key("a", "b.jsonl") == "evalai/public/a/b.jsonl"
    assert private_key("a", "b.jsonl") == "evalai/private/a/b.jsonl"


def test_public_material_has_a_public_url(storage):
    key = public_key("challenges", "c1", "inputs.jsonl")
    storage.put(key, b"x")

    assert storage.public_url(key).endswith("/evalai/public/challenges/c1/inputs.jsonl")


def test_private_material_has_no_public_url(storage):
    key = private_key("challenges", "c1", "ground_truth.jsonl")
    storage.put(key, b"x")

    with pytest.raises(PrivateObjectError):
        storage.public_url(key)


def test_deleted_objects_are_gone(storage):
    key = private_key("challenges", "c1", "ground_truth.jsonl")
    storage.put(key, b"x")
    storage.delete(key)

    with pytest.raises(ObjectNotFound):
        storage.get(key)
    assert storage.objects == {}


def test_deleting_a_missing_object_is_not_an_error(storage):
    storage.delete(public_key("never", "stored"))


def test_the_fake_shows_what_it_holds(storage):
    key = public_key("challenges", "c1", "inputs.jsonl")
    storage.put(key, b"x", content_type="application/jsonl")

    assert storage.objects == {key: b"x"}
    assert storage.content_types[key] == "application/jsonl"
