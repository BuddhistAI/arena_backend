"""Delete and Resync: an admin deletes a Challenge, which leaves the arena and
has its repo archived by the Sync Worker; an admin asks for a Resync, which
rebuilds the repo from the arena's data. Tests drain the worker synchronously."""

from test_create_challenge import category, create_challenge  # noqa: F401  (category is a fixture)


def created(response):
    assert response.status_code == 201, response.text
    return response.json()


def delete(client, challenge, headers):
    return client.delete(f"/challenges/remove/{challenge['id']}", headers=headers)


def listed_ids(client):
    response = client.get("/challenges/list")
    assert response.status_code == 200, response.text
    return [c["id"] for c in response.json()]


def test_deleting_a_challenge_archives_its_repo_and_removes_it_from_the_arena(
    client, admin_headers, category, repo_host, sync_worker
):
    challenge = created(create_challenge(client, admin_headers, category, title="Tibetan OCR"))
    sync_worker.drain()

    response = delete(client, challenge, admin_headers)
    assert response.status_code == 200, response.text
    sync_worker.drain()

    assert repo_host.repos["challenge-tibetan-ocr"].archived is True
    assert challenge["id"] not in listed_ids(client)
    assert client.get(f"/challenges/{challenge['id']}").status_code == 404


def test_a_non_admin_cannot_delete_a_challenge(
    client, admin_headers, user_headers, category, repo_host, sync_worker
):
    challenge = created(create_challenge(client, admin_headers, category, title="Tibetan OCR"))
    sync_worker.drain()

    assert delete(client, challenge, user_headers).status_code == 403
    sync_worker.drain()

    assert repo_host.repos["challenge-tibetan-ocr"].archived is False
    assert challenge["id"] in listed_ids(client)


def test_a_challenge_deleted_before_its_repo_was_published_never_gets_a_repo(
    client, admin_headers, category, repo_host, sync_worker
):
    challenge = created(create_challenge(client, admin_headers, category, title="Tibetan OCR"))

    assert delete(client, challenge, admin_headers).status_code == 200
    sync_worker.drain()

    assert repo_host.repos == {}
    assert challenge["id"] not in listed_ids(client)


def test_archiving_is_retried_when_the_repo_host_fails(
    client, admin_headers, category, repo_host, sync_worker
):
    challenge = created(create_challenge(client, admin_headers, category, title="Tibetan OCR"))
    sync_worker.drain()
    delete(client, challenge, admin_headers)
    repo_host.fail_next(2, "GitHub is down")

    sync_worker.drain()

    assert repo_host.repos["challenge-tibetan-ocr"].archived is True


def test_a_deleted_challenge_can_be_neither_edited_nor_deleted_again(
    client, admin_headers, category, sync_worker
):
    challenge = created(create_challenge(client, admin_headers, category, title="Tibetan OCR"))
    delete(client, challenge, admin_headers)

    edit = client.patch(
        f"/challenges/update/{challenge['id']}", data={"description": "Still here?"}, headers=admin_headers
    )

    assert edit.status_code == 404
    assert delete(client, challenge, admin_headers).status_code == 404
