"""

Revision ID: 0314_lower_api_rate_limit
Revises: 0312_update_branding_request
Create Date: 2021-01-08 16:13:25

"""
from alembic import op
import sqlalchemy as sa


revision = '0314_lower_api_rate_limit'
down_revision = '0312_update_branding_request'


def upgrade():
    op.execute("ALTER TABLE services ALTER rate_limit DROP DEFAULT")
    op.execute("ALTER TABLE services_history ALTER rate_limit DROP DEFAULT")

def downgrade():
    op.execute("ALTER TABLE services ALTER rate_limit SET DEFAULT '1000'")
    op.execute("ALTER TABLE services_history ALTER rate_limit SET DEFAULT '1000'")
