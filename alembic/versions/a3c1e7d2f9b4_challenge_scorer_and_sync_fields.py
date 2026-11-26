"""Challenge Scorer, storage and sync fields; delete legacy challenges

Every existing Challenge is deleted with its Submissions and Results: they
are EvalAI-shaped (a ground-truth URL scored with CER/WER) and cannot be
expressed in the new model. Arena Challenges, users, models and categories
are not touched. The legacy S3 objects are removed separately by
scripts/delete_legacy_challenge_objects.py.

The downgrade restores the columns but not the deleted rows.

Revision ID: a3c1e7d2f9b4
Revises: 65735e75bac9
Create Date: 2026-11-26 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a3c1e7d2f9b4'
down_revision: Union[str, Sequence[str], None] = '65735e75bac9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Result -> Submission -> Challenge. Every Result belongs to a
    # Submission and every Submission to a Challenge.
    op.execute("DELETE FROM result WHERE submission_id IN (SELECT id FROM submission)")
    op.execute("DELETE FROM submission")
    op.execute("DELETE FROM challenge")

    op.drop_column('challenge', 'ground_truth')

    op.add_column('challenge', sa.Column('scorer_name', sa.String(), nullable=False))
    op.add_column('challenge', sa.Column('scorer_params', sa.JSON(), nullable=False))
    op.add_column('challenge', sa.Column('direction', sa.String(), nullable=False))
    op.add_column('challenge', sa.Column('scorer_label', sa.String(), nullable=False))
    op.add_column('challenge', sa.Column('package_version', sa.String(), nullable=False))
    op.add_column('challenge', sa.Column('inputs_key', sa.String(), nullable=False))
    op.add_column('challenge', sa.Column('media_archive_key', sa.String(), nullable=True))
    op.add_column('challenge', sa.Column('ground_truth_key', sa.String(), nullable=False))
    op.add_column('challenge', sa.Column('repo_name', sa.String(), nullable=False))
    op.add_column(
        'challenge',
        sa.Column('sync_status', sa.String(), nullable=False, server_default='pending'),
    )
    op.add_column(
        'challenge',
        sa.Column('sync_attempts', sa.Integer(), nullable=False, server_default='0'),
    )
    op.add_column('challenge', sa.Column('last_sync_error', sa.String(), nullable=True))
    op.add_column('challenge', sa.Column('locked_at', sa.DateTime(), nullable=True))

    op.create_unique_constraint('challenge_title_key', 'challenge', ['title'])
    op.create_unique_constraint('challenge_repo_name_key', 'challenge', ['repo_name'])
    op.create_check_constraint(
        'ck_challenge_sync_status', 'challenge', "sync_status IN ('pending', 'synced', 'failed')"
    )
    op.create_check_constraint(
        'ck_challenge_direction', 'challenge', "direction IN ('lower_is_better', 'higher_is_better')"
    )


def downgrade() -> None:
    op.drop_constraint('ck_challenge_direction', 'challenge', type_='check')
    op.drop_constraint('ck_challenge_sync_status', 'challenge', type_='check')
    op.drop_constraint('challenge_repo_name_key', 'challenge', type_='unique')
    op.drop_constraint('challenge_title_key', 'challenge', type_='unique')

    for column in (
        'locked_at', 'last_sync_error', 'sync_attempts', 'sync_status', 'repo_name',
        'ground_truth_key', 'media_archive_key', 'inputs_key', 'package_version',
        'scorer_label', 'direction', 'scorer_params', 'scorer_name',
    ):
        op.drop_column('challenge', column)

    # Rows created on the new schema have no ground-truth URL.
    op.add_column(
        'challenge',
        sa.Column('ground_truth', sa.String(), nullable=False, server_default=''),
    )
    op.alter_column('challenge', 'ground_truth', server_default=None)
