"""The Alembic migration that moves an existing arena onto the new Challenge.

The test takes the test database back to the previous revision (the legacy
Challenge with a ground-truth URL), fills it with legacy rows, upgrades, and
checks what survived and that the result matches the models.
"""

import os
import uuid

import pytest
from sqlalchemy import text

PREVIOUS_REVISION = "65735e75bac9"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def alembic_cfg(app):
    from alembic.config import Config

    return Config(os.path.join(ROOT, "alembic.ini"))


@pytest.fixture
def legacy_database(app, db, alembic_cfg):
    """The test database at the previous revision, holding a legacy Challenge
    with a Submission and a Result, next to an Arena Challenge, a user, a
    model and a category. Upgraded back to head afterwards."""
    from alembic import command

    import database
    from models import ArenaChallenge, Category, Model, TextCategory, User

    command.stamp(alembic_cfg, "head")
    command.downgrade(alembic_cfg, PREVIOUS_REVISION)
    try:
        user = User(id="auth0|legacy", username="legacy", email="legacy@example.org", role="admin")
        category = Category(name="OCR", created_by=user.id, updated_by=user.id)
        model = Model(name="legacy-ocr-model", created_by=user.id, updated_by=user.id)
        text_category = TextCategory(name="Sutra")
        db.add_all([user, category, model, text_category])
        db.flush()
        db.add(
            ArenaChallenge(
                text_category_id=text_category.id,
                user_id=user.id,
                challenge_name="Translate the Heart Sutra",
                from_language="bo",
                to_language="en",
            )
        )
        db.commit()

        challenge_id, submission_id = uuid.uuid4(), uuid.uuid4()
        with database.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO challenge (id, title, category_id, created_by, ground_truth, "
                    "created_at, updated_at) VALUES (:id, 'Legacy OCR', :cat, :user, "
                    "'https://bucket.s3.us-east-1.amazonaws.com/evalai/ground_truth/x/gt.json', "
                    "now(), now())"
                ),
                {"id": challenge_id, "cat": category.id, "user": user.id},
            )
            conn.execute(
                text(
                    "INSERT INTO submission (id, user_id, model_id, challenge_id, status, "
                    "created_at, updated_at) VALUES (:id, :user, :model, :challenge, "
                    "'COMPLETED', now(), now())"
                ),
                {"id": submission_id, "user": user.id, "model": model.id, "challenge": challenge_id},
            )
            conn.execute(
                text(
                    "INSERT INTO result (id, type, user_id, submission_id, score, created_at, "
                    "created_by, updated_at, updated_by) VALUES (:id, 'CER', :user, :sub, 0.1, "
                    "now(), 'x', now(), 'x')"
                ),
                {"id": uuid.uuid4(), "user": user.id, "sub": submission_id},
            )
        yield
    finally:
        db.rollback()
        command.upgrade(alembic_cfg, "head")


def _count(table):
    import database

    with database.engine.connect() as conn:
        return conn.execute(text(f'SELECT count(*) FROM "{table}"')).scalar()


def test_upgrade_deletes_legacy_challenges_with_their_submissions_and_results(
    legacy_database, alembic_cfg
):
    from alembic import command

    command.upgrade(alembic_cfg, "head")

    assert _count("challenge") == 0
    assert _count("submission") == 0
    assert _count("result") == 0


def test_upgrade_leaves_arena_challenges_users_models_and_categories(legacy_database, alembic_cfg):
    from alembic import command

    command.upgrade(alembic_cfg, "head")

    assert _count("arena_challenge") == 1
    assert _count("user") == 1
    assert _count("model") == 1
    assert _count("category") == 1


@pytest.mark.parametrize("table", ["challenge", "submission"])
def test_upgraded_table_matches_the_model(legacy_database, alembic_cfg, table):
    from alembic import command
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    import database

    command.upgrade(alembic_cfg, "head")

    with database.engine.connect() as conn:
        diffs = compare_metadata(MigrationContext.configure(conn), database.Base.metadata)
    table_diffs = [
        d
        for d in diffs
        if f"table=<{table}>" in repr(d) or f"'{table}'" in repr(d)
        # Every model declares its primary key ``unique=True`` too; create_all
        # folds that into the primary key, autogenerate reports it on every
        # table. Not a migration difference.
        if repr(d) != f"('add_constraint', UniqueConstraint(Column('id', NullType(), table=<{table}>)))"
    ]
    assert table_diffs == []


def test_downgrade_from_head_restores_the_previous_submission_table(legacy_database, alembic_cfg):
    """The Submission fields migration can be undone back to ``b7d2e4f1c8a6``."""
    from alembic import command
    from sqlalchemy import inspect

    import database

    command.upgrade(alembic_cfg, "head")
    command.downgrade(alembic_cfg, "b7d2e4f1c8a6")

    columns = {c["name"] for c in inspect(database.engine).get_columns("submission")}
    assert "dataset_url" in columns
    assert not columns & {
        "type", "name", "model_link", "predictions_url", "headline_metric", "score_details",
        "preferred_model", "license", "artifact_url", "repo_path", "sync_status", "deleted_at",
    }


def test_downgrade_drops_the_prompt_submission_fields(legacy_database, alembic_cfg):
    """The Prompt fields migration can be undone back to ``c4e8a2b6d1f3``,
    keeping the Model Submission fields."""
    from alembic import command
    from sqlalchemy import inspect

    import database

    command.upgrade(alembic_cfg, "head")
    command.downgrade(alembic_cfg, "c4e8a2b6d1f3")

    columns = {c["name"] for c in inspect(database.engine).get_columns("submission")}
    assert "predictions_url" in columns
    assert not columns & {"preferred_model", "license", "artifact_url", "deleted_at"}


def test_downgrade_drops_the_submission_repo_sync_fields(legacy_database, alembic_cfg):
    """The repo sync migration can be undone back to ``d5f1b3c7e9a2``,
    keeping the Prompt Submission fields."""
    from alembic import command
    from sqlalchemy import inspect

    import database

    command.upgrade(alembic_cfg, "head")
    command.downgrade(alembic_cfg, "d5f1b3c7e9a2")

    columns = {c["name"] for c in inspect(database.engine).get_columns("submission")}
    assert "preferred_model" in columns
    assert not columns & {"repo_path", "sync_status", "deleted_at"}


def test_downgrade_drops_the_submission_deletion_marker(legacy_database, alembic_cfg):
    """The deletion marker migration can be undone back to ``d5f1a3c7e9b2``,
    keeping the repo sync fields."""
    from alembic import command
    from sqlalchemy import inspect

    import database

    command.upgrade(alembic_cfg, "head")
    command.downgrade(alembic_cfg, "d5f1a3c7e9b2")

    columns = {c["name"] for c in inspect(database.engine).get_columns("submission")}
    assert "sync_status" in columns
    assert "deleted_at" not in columns
