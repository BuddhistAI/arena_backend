"""Submission Preferred Model, license and Artifact key

Prompt (and later Skill) Submissions carry the exact Preferred Model id they
were run with (free text, not a reference to a model), a license, and the
private storage key of their Artifact. All three are null on Model
Submissions, so existing rows need no backfill.

Revision ID: d5f1b3c7e9a2
Revises: c4e8a2b6d1f3
Create Date: 2026-11-26 18:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd5f1b3c7e9a2'
down_revision: Union[str, Sequence[str], None] = 'c4e8a2b6d1f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('submission', sa.Column('preferred_model', sa.String(), nullable=True))
    op.add_column('submission', sa.Column('license', sa.String(), nullable=True))
    op.add_column('submission', sa.Column('artifact_url', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('submission', 'artifact_url')
    op.drop_column('submission', 'license')
    op.drop_column('submission', 'preferred_model')
