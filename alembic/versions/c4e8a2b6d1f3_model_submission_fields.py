"""Submission type, name, model link, predictions key and score

A Submission now has a Submission Type and a name, an optional model link,
the private storage key of its Predictions File (``predictions_url``,
replacing ``dataset_url``) and the Headline Metric and details the Scorer
returned. ``model_id`` is set only for Model Submissions.

No Submission can exist at the previous revision: ``a3c1e7d2f9b4`` deleted
them all and creating one answered 501 since. Any row left over cannot be
given a type or a Predictions File, so it is deleted with its Results.

Revision ID: c4e8a2b6d1f3
Revises: b7d2e4f1c8a6
Create Date: 2026-11-26 16:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c4e8a2b6d1f3'
down_revision: Union[str, Sequence[str], None] = 'b7d2e4f1c8a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DELETE FROM result")
    op.execute("DELETE FROM submission")
    op.alter_column('submission', 'dataset_url', new_column_name='predictions_url', nullable=False)
    op.alter_column('submission', 'model_id', nullable=True)
    op.add_column('submission', sa.Column('type', sa.String(), nullable=False))
    op.add_column('submission', sa.Column('name', sa.String(), nullable=False))
    op.add_column('submission', sa.Column('model_link', sa.String(), nullable=True))
    op.add_column('submission', sa.Column('headline_metric', sa.Float(), nullable=True))
    op.add_column('submission', sa.Column('score_details', sa.JSON(), nullable=True))
    op.create_check_constraint(
        'ck_submission_type', 'submission', "type IN ('model', 'prompt', 'skill')"
    )


def downgrade() -> None:
    op.execute("DELETE FROM result WHERE submission_id IN (SELECT id FROM submission WHERE model_id IS NULL)")
    op.execute("DELETE FROM submission WHERE model_id IS NULL")
    op.drop_constraint('ck_submission_type', 'submission', type_='check')
    op.drop_column('submission', 'score_details')
    op.drop_column('submission', 'headline_metric')
    op.drop_column('submission', 'model_link')
    op.drop_column('submission', 'name')
    op.drop_column('submission', 'type')
    op.alter_column('submission', 'model_id', nullable=False)
    op.alter_column('submission', 'predictions_url', new_column_name='dataset_url', nullable=True)
