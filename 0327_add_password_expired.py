"""

Revision ID: 0327_add_password_expired
Revises: 0326_add_queue_notifications
Create Date: 2022-04-06 13:00:00

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0327_add_password_expired"
down_revision = "0326_add_queue_notifications"

user = "postgres"
timeout = 1200  # in seconds, i.e. 20 minutes


def upgrade():
    op.add_column("users", sa.Column("password_expired", sa.Boolean(), nullable=False))


def downgrade():
    op.drop_column("users", "password_expired")
