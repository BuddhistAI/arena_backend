"""Challenge deleted_at: a deleted Challenge is kept as a tombstone

Deleting a Challenge sets ``deleted_at`` and queues archiving of its repo
through the Sync Worker. The row stays so the archive request survives a
restart, and so its unique repo name is never reused.

Revision ID: b7d2e4f1c8a6
Revises: a3c1e7d2f9b4
Create Date: 2026-11-26 15:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'b7d2e4f1c8a6'
down_revision: Union[str, Sequence[str], None] = 'a3c1e7d2f9b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('challenge', sa.Column('deleted_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('challenge', 'deleted_at')
