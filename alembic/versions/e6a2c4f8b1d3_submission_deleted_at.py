"""Submission deleted_at: a deleted Submission is kept as a marker

Deleting a Submission sets ``deleted_at`` instead of removing the row, so the
daily limit still counts it. A deleted Submission is gone from the arena's
API, and the Sync Worker removes its folder from the Challenge Repo.

Revision ID: e6a2c4f8b1d3
Revises: d5f1a3c7e9b2
Create Date: 2026-11-26 21:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'e6a2c4f8b1d3'
down_revision: Union[str, Sequence[str], None] = 'd5f1a3c7e9b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('submission', sa.Column('deleted_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('submission', 'deleted_at')
