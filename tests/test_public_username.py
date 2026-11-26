"""Public username: a user chooses the name their Submissions are published
under with ``PATCH /users/me``. It must be unique and 3 to 30 lowercase ASCII
letters, digits, ``-`` or ``_``. Until they choose, it stays the email's local
part set at first sign-in."""

import pytest


def rename(client, headers, username):
    return client.patch("/users/me", json={"username": username}, headers=headers)


def me(client, headers):
    response = client.get("/users/me", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_a_user_can_change_their_username(client, user_headers):
    """The new username is returned and read back from ``/users/me``."""
    response = rename(client, user_headers, "tenzin_dorje-2")

    assert response.status_code == 200, response.text
    assert response.json()["username"] == "tenzin_dorje-2"
    assert me(client, user_headers)["username"] == "tenzin_dorje-2"


@pytest.mark.parametrize(
    "username",
    ["ab", "a" * 31, "Tenzin", "tenzin.dorje", "tenzin dorje", "ཀཁག", "tenzin@x", "tenzin\n", "", None],
)
def test_an_invalid_username_is_refused(client, make_user, headers_for, username):
    """Too short, too long, upper case, any character other than lowercase
    ASCII letters, digits, ``-`` and ``_``, or no username at all is refused
    with 422 and the username is unchanged."""
    user = make_user("user", name="tenzin")

    response = rename(client, headers_for(user), username)

    assert response.status_code == 422, response.text
    assert me(client, headers_for(user))["username"] == "tenzin"


def test_a_username_someone_else_has_is_refused_as_taken(client, make_user, headers_for):
    """Choosing another user's username answers 409 and changes nothing."""
    make_user("user", name="pema")
    user = make_user("user", name="tenzin")

    response = rename(client, headers_for(user), "pema")

    assert response.status_code == 409, response.text
    assert me(client, headers_for(user))["username"] == "tenzin"


def test_choosing_your_own_username_again_is_accepted(client, make_user, headers_for):
    """Saving the username you already have is not a conflict."""
    user = make_user("user", name="tenzin")

    response = rename(client, headers_for(user), "tenzin")

    assert response.status_code == 200, response.text
