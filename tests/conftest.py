"""API test harness for arena_backend.

Tests talk to the app through FastAPI's TestClient, backed by a real Postgres
database named by ``TEST_DATABASE_URL``. The schema is rebuilt from the models
once per run and every table is emptied after each test.

Authentication is replaced by a stand-in that treats the bearer token as a
user id, so ``headers_for(user)`` signs a request in as that user.
"""

import os
from urllib.parse import urlparse

import pytest


def pytest_configure(config):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        raise pytest.UsageError(
            "Set TEST_DATABASE_URL to a disposable Postgres database, e.g. "
            "postgresql://openpecha:openpecha@localhost:5432/arena_test_01"
        )
    # The harness drops and recreates the public schema, so refuse any
    # database whose name does not say it is for tests.
    if "test" not in urlparse(url).path.lower():
        raise pytest.UsageError(
            f"TEST_DATABASE_URL must name a test database (got {url!r})"
        )
    # database.py builds its engine from DATABASE_URL at import time.
    os.environ["DATABASE_URL"] = url


@pytest.fixture(scope="session")
def app():
    from sqlalchemy import text

    import database
    import models  # noqa: F401  registers every table on Base.metadata

    with database.engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    database.Base.metadata.create_all(bind=database.engine)

    import main

    return main.app


@pytest.fixture(autouse=True)
def _clean_tables(app):
    yield
    import database

    tables = ", ".join(f'"{t.name}"' for t in database.Base.metadata.sorted_tables)
    with database.engine.begin() as conn:
        conn.exec_driver_sql(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")


@pytest.fixture
def db(app):
    """A session on the test database, for setup the API cannot do."""
    import database

    session = database.SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _test_client(app):
    """Starlette 0.32's TestClient passes ``app=`` to httpx.Client, which
    httpx 0.28 (required by google-genai) no longer accepts. A mixin placed
    between the two in the MRO drops that argument."""
    import httpx
    from fastapi.testclient import TestClient

    class _DropAppArgument(httpx.Client):
        def __init__(self, *args, app=None, **kwargs):
            super().__init__(*args, **kwargs)

    class ApiTestClient(TestClient, _DropAppArgument):
        pass

    return ApiTestClient(app)


@pytest.fixture
def storage():
    """The in-memory object storage the API writes to during a test."""
    from ports.object_storage import InMemoryObjectStorage

    return InMemoryObjectStorage()


@pytest.fixture
def repo_host():
    """The in-memory challenge repo host the API publishes to during a test."""
    from ports.repo_host import InMemoryRepoHost

    return InMemoryRepoHost()


@pytest.fixture
def client(app, storage, repo_host):
    from fastapi import Depends, HTTPException
    from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

    import auth
    from database import get_db
    from models.user import User
    from ports.object_storage import get_object_storage
    from ports.repo_host import get_repo_host

    def authenticate_by_user_id(
        credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer()),
        db=Depends(get_db),
    ):
        user = db.get(User, credentials.credentials)
        if user is None:
            raise HTTPException(status_code=401, detail="Authentication failed")
        return user

    app.dependency_overrides[auth.authenticate] = authenticate_by_user_id
    app.dependency_overrides[get_object_storage] = lambda: storage
    app.dependency_overrides[get_repo_host] = lambda: repo_host
    # Not used as a context manager, so startup hooks (background workers,
    # cache cleanup) do not run during tests.
    yield _test_client(app)
    app.dependency_overrides.clear()


@pytest.fixture
def make_user(db):
    """Create a user with the given role and return it."""
    from models.user import User

    def _make_user(role="user", name=None):
        name = name or f"{role}-{db.query(User).count() + 1}"
        user = User(id=f"auth0|{name}", username=name, email=f"{name}@example.org", role=role)
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    return _make_user


def headers_for(user):
    """Request headers that sign a request in as ``user``."""
    return {"Authorization": f"Bearer {user.id}"}


@pytest.fixture(name="headers_for")
def headers_for_fixture():
    """``headers_for(user)`` as a fixture, so test modules need no import."""
    return headers_for


@pytest.fixture
def admin_headers(make_user):
    return headers_for(make_user("admin"))


@pytest.fixture
def user_headers(make_user):
    return headers_for(make_user("user"))


@pytest.fixture
def make_sync_worker(app, storage, repo_host):
    """``make_sync_worker(**options)`` builds a Sync Worker that publishes to
    the in-memory repo host, e.g. with a lower committed-Inputs limit."""
    import database
    from sync_worker import SyncWorker

    def _make_sync_worker(**options):
        return SyncWorker(
            session_factory=database.SessionLocal, storage=storage, repo_host=repo_host, **options
        )

    return _make_sync_worker


@pytest.fixture
def sync_worker(make_sync_worker):
    """The Sync Worker with its production settings. Tests call
    ``sync_worker.drain()`` to run it until no sync work is left."""
    return make_sync_worker()
