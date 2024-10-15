"""

Revision ID: 0461_add_pinpoint_fields
Revises: 0460_new_service_columns
Create Date: 2024-10-15 18:24:22.926597

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0461_add_pinpoint_fields'
down_revision = '0460_new_service_columns'


def upgrade():
    op.add_column("notifications", sa.Column("sms_total_message_price", sa.Float(), nullable=True))
    op.add_column("notifications", sa.Column("sms_total_carrier_fee", sa.Float(), nullable=True))
    op.add_column("notifications", sa.Column("sms_iso_country_code", sa.VARCHAR(), nullable=True))
    op.add_column("notifications", sa.Column("sms_carrier_name", sa.VARCHAR(), nullable=True))
    op.add_column("notifications", sa.Column("sms_message_encoding", sa.VARCHAR(), nullable=True))
    op.add_column("notifications", sa.Column("sms_origination_phone_number", sa.VARCHAR(), nullable=True))
    op.add_column("notification_history", sa.Column("sms_total_message_price", sa.Float(), nullable=True))
    op.add_column("notification_history", sa.Column("sms_total_carrier_fee", sa.Float(), nullable=True))
    op.add_column("notification_history", sa.Column("sms_iso_country_code", sa.VARCHAR(), nullable=True))
    op.add_column("notification_history", sa.Column("sms_carrier_name", sa.VARCHAR(), nullable=True))
    op.add_column("notification_history", sa.Column("sms_message_encoding", sa.VARCHAR(), nullable=True))
    op.add_column("notification_history", sa.Column("sms_origination_phone_number", sa.VARCHAR(), nullable=True))
    

def downgrade():
    op.drop_column("notifications", "sms_total_message_price")
    op.drop_column("notifications", "sms_total_carrier_fee")
    op.drop_column("notifications", "sms_iso_country_code")
    op.drop_column("notifications", "sms_carrier_name")
    op.drop_column("notifications", "sms_message_encoding")
    op.drop_column("notifications", "sms_origination_phone_number")
    op.drop_column("notification_history", "sms_total_message_price")
    op.drop_column("notification_history", "sms_total_carrier_fee")
    op.drop_column("notification_history", "sms_iso_country_code")
    op.drop_column("notification_history", "sms_carrier_name")
    op.drop_column("notification_history", "sms_message_encoding")
    op.drop_column("notification_history", "sms_origination_phone_number")
    
    
