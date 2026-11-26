"""Submission repo path and sync status

A scored Submission is published to its Challenge Repo. ``repo_path`` is its
folder there, set once published; ``sync_status`` is ``pending`` until then,
``synced`` after, and ``failed`` when its Challenge's sync ran out of
attempts.

Revision ID: d5f1a3c7e9b2
Revises: d5f1b3c7e9a2
Create Date: 2026-11-26 18:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd5f1a3c7e9b2'
down_revision: Union[str, Sequence[str], None] = 'd5f1b3c7e9a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('submission', sa.Column('repo_path', sa.String(), nullable=True))
    op.add_column(
        'submission', sa.Column('sync_status', sa.String(), nullable=False, server_default='pending')
    )
    op.create_check_constraint(
        'ck_submission_sync_status', 'submission', "sync_status IN ('pending', 'synced', 'failed')"
    )


def downgrade() -> None:
    op.drop_constraint('ck_submission_sync_status', 'submission', type_='check')
    op.drop_column('submission', 'sync_status')
    op.drop_column('submission', 'repo_path')
