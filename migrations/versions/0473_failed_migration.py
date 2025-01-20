"""

Revision ID: 0473_failed_migration
Revises: 0472_add_direct_email_2
Create Date: 2025-01-13 00:00:00

"""
from datetime import datetime

from alembic import op
from flask import current_app

revision = "0473_failed_migration"
down_revision = "0472_add_direct_email_2"


def upgrade():
    pass
    op.execute("INSERT INTO no-such-table (id, name) VALUES ('1', 'nope')")


def downgrade():
    pass
