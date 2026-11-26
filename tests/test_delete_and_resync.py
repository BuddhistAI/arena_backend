"""Delete and Resync: an admin deletes a Challenge, which leaves the arena and
has its repo archived by the Sync Worker; an admin asks for a Resync, which
rebuilds the repo from the arena's data. Tests drain the worker synchronously."""

from test_create_challenge import admin_read, category, create_challenge  # noqa: F401  (category is a fixture)


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


def resync(client, challenge, headers):
    return client.post(f"/challenges/resync/{challenge['id']}", headers=headers)


read = admin_read


def test_a_failed_sync_is_published_after_a_resync(
    client, admin_headers, category, repo_host, sync_worker
):
    challenge = created(create_challenge(client, admin_headers, category, title="Tibetan OCR"))
    repo_host.fail_next(5, "Bad credentials")
    sync_worker.drain()
    assert read(client, challenge)["sync_status"] == "failed"

    response = resync(client, challenge, admin_headers)
    assert response.status_code == 200, response.text
    assert (response.json()["sync_status"], response.json()["sync_attempts"]) == ("pending", 0)
    sync_worker.drain()

    synced = read(client, challenge)
    assert synced["sync_status"] == "synced"
    assert synced["last_sync_error"] is None
    assert "challenge.json" in repo_host.repos["challenge-tibetan-ocr"].files


def test_a_non_admin_cannot_resync_a_challenge(
    client, admin_headers, user_headers, category, repo_host, sync_worker
):
    challenge = created(create_challenge(client, admin_headers, category, title="Tibetan OCR"))
    repo_host.fail_next(5)
    sync_worker.drain()

    assert resync(client, challenge, user_headers).status_code == 403
    assert read(client, challenge)["sync_status"] == "failed"


def admin_list(client, headers):
    return client.get("/challenges/admin/list", headers=headers)


def test_a_failed_archive_is_shown_to_admins_and_retried_by_a_resync(
    client, admin_headers, category, repo_host, sync_worker
):
    challenge = created(create_challenge(client, admin_headers, category, title="Tibetan OCR"))
    sync_worker.drain()
    delete(client, challenge, admin_headers)
    repo_host.fail_next(5, "GitHub is down")
    sync_worker.drain()

    response = admin_list(client, admin_headers)
    assert response.status_code == 200, response.text
    [listed] = response.json()
    assert listed["id"] == challenge["id"]
    assert listed["deleted_at"] is not None
    assert listed["sync_status"] == "failed"
    assert "GitHub is down" in listed["last_sync_error"]
    assert repo_host.repos["challenge-tibetan-ocr"].archived is False

    response = resync(client, challenge, admin_headers)
    assert response.status_code == 200, response.text
    sync_worker.drain()

    assert repo_host.repos["challenge-tibetan-ocr"].archived is True
    [archived] = admin_list(client, admin_headers).json()
    assert (archived["sync_status"], archived["last_sync_error"]) == ("synced", None)
    assert challenge["id"] not in listed_ids(client)


def test_admins_read_a_deleted_challenge_that_the_arena_no_longer_shows(
    client, admin_headers, category, sync_worker
):
    challenge = created(create_challenge(client, admin_headers, category, title="Tibetan OCR"))
    delete(client, challenge, admin_headers)

    response = client.get(f"/challenges/admin/{challenge['id']}", headers=admin_headers)

    assert response.status_code == 200, response.text
    assert response.json()["title"] == "Tibetan OCR"
    assert response.json()["deleted_at"] is not None
    assert client.get(f"/challenges/{challenge['id']}").status_code == 404


def test_only_admins_see_the_admin_view_of_challenges(client, admin_headers, user_headers, category):
    challenge = created(create_challenge(client, admin_headers, category, title="Tibetan OCR"))

    assert admin_list(client, user_headers).status_code == 403
    assert client.get(f"/challenges/admin/{challenge['id']}", headers=user_headers).status_code == 403
    assert client.get("/challenges/admin/list").status_code in (401, 403)


def test_a_resync_rebuilds_the_repo_and_drops_files_added_on_github(
    client, admin_headers, category, repo_host, sync_worker
):
    challenge = created(create_challenge(client, admin_headers, category, title="Tibetan OCR"))
    sync_worker.drain()
    repo = repo_host.repos["challenge-tibetan-ocr"]
    published = dict(repo.files)
    # Someone edits the repo by hand on GitHub.
    repo.files["notes/stray.txt"] = b"added by hand\n"
    repo.files["README.md"] = b"# Edited on GitHub\n"

    resync(client, challenge, admin_headers)
    sync_worker.drain()

    assert repo.files == published
