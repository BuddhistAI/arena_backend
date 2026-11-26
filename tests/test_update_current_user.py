"""What a user may change about themselves with ``PATCH /users/me``. Their
role and email are not among it: an admin role grants seeing others'
unscored Submissions, and the email comes from sign-in."""

import pytest


def me(client, headers):
    response = client.get("/users/me", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("change", [{"role": "admin"}, {"email": "someone-else@example.org"}])
def test_a_user_cannot_set_their_own_role_or_email(client, make_user, headers_for, change):
    """Sending a role or email is refused with 422 and changes nothing,
    not even the username sent with it."""
    user = make_user("user", name="tenzin")

    response = client.patch("/users/me", json={"username": "pema", **change}, headers=headers_for(user))

    assert response.status_code == 422, response.text
    after = me(client, headers_for(user))
    assert (after["role"], after["email"], after["username"]) == ("user", "tenzin@example.org", "tenzin")


def test_a_user_who_sends_an_admin_role_still_cannot_see_others_unscored_submissions(
    client, make_user, headers_for
):
    """The refused role change grants nothing: admin-only endpoints still answer 403."""
    user = make_user("user", name="tenzin")
    client.patch("/users/me", json={"role": "admin"}, headers=headers_for(user))

    response = client.get("/submissions", headers=headers_for(user))

    assert response.status_code == 403, response.text
