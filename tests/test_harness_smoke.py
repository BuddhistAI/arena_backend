"""Smoke tests proving the API test harness works end to end."""

import pytest


@pytest.mark.parametrize("run", [1, 2])
def test_admin_creates_a_category_and_sees_it_listed(client, admin_headers, run):
    # Running twice with the same name proves rows are cleaned between tests:
    # the API rejects a duplicate category name with 400.
    created = client.post("/categories/create", json={"name": "OCR"}, headers=admin_headers)
    assert created.status_code == 201, created.text

    listed = client.get("/categories")
    assert listed.status_code == 200
    assert [c["name"] for c in listed.json()] == ["OCR"]


def test_request_without_a_token_is_rejected(client):
    response = client.post("/categories/create", json={"name": "OCR"})
    assert response.status_code in (401, 403)
