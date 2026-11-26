"""Submission published_submitter: the username a Submission was published under

Users can now rename themselves. A Submission's ``submission.json`` keeps the
username it was first published under, so later syncs of its Challenge do not
rewrite it. Submissions already published were published under their
submitter's current username, since nobody could rename before.

Revision ID: f7b3d5a9c2e4
Revises: e6a2c4f8b1d3
Create Date: 2026-11-26 23:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f7b3d5a9c2e4'
down_revision: Union[str, Sequence[str], None] = 'e6a2c4f8b1d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('submission', sa.Column('published_submitter', sa.String(), nullable=True))
    op.execute(
        'UPDATE submission SET published_submitter = "user".username '
        'FROM "user" WHERE "user".id = submission.user_id AND submission.repo_path IS NOT NULL'
    )


def downgrade() -> None:
    op.drop_column('submission', 'published_submitter')
