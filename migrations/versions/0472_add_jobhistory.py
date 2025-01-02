"""empty message

Revision ID:0472_add_jobhistory"
Revises: 0471_edit_limit_emails2
Create Date: 2016-06-01 14:17:01.963181

"""

from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0472_add_jobhistory"
down_revision = "0471_edit_limit_emails2"


def upgrade():
    op.create_table(
        "jobs_history",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False, default=sa.text("uuid_generate_v4()")),
        sa.Column("version", sa.Integer, nullable=False, default=0),
        sa.Column("original_file_name", sa.String, nullable=False),
        sa.Column("service_id", UUID(as_uuid=True), sa.ForeignKey("services.id"), nullable=False),
        sa.Column("template_id", UUID(as_uuid=True), sa.ForeignKey("templates.id"), nullable=True),
        sa.Column("template_version", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False, default=datetime.utcnow()),
        sa.Column("updated_at", sa.DateTime, nullable=True, onupdate=datetime.utcnow()),
        sa.Column("notification_count", sa.Integer, nullable=False),
        sa.Column("notifications_sent", sa.Integer, nullable=False, default=0),
        sa.Column("notifications_delivered", sa.Integer, nullable=False, default=0),
        sa.Column("notifications_failed", sa.Integer, nullable=False, default=0),
        sa.Column("processing_started", sa.DateTime, nullable=True),
        sa.Column("processing_finished", sa.DateTime, nullable=True),
        sa.Column("created_by_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("api_key_id", UUID(as_uuid=True), sa.ForeignKey("api_keys.id"), nullable=True),
        sa.Column("scheduled_for", sa.DateTime, nullable=True),
        sa.Column("job_status", sa.String(255), sa.ForeignKey("job_status.name"), nullable=False, default="pending"),
        sa.Column("archived", sa.Boolean, nullable=False, default=False),
        sa.Column("sender_id", UUID(as_uuid=True), nullable=True),
        sa.Index("ix_jobs_history_service_id", "service_id"),
        sa.Index("ix_jobs_history_template_id", "template_id"),
        sa.Index("ix_jobs_history_created_by_id", "created_by_id"),
        sa.Index("ix_jobs_history_api_key_id", "api_key_id"),
        sa.Index("ix_jobs_history_job_status", "job_status"),
        sa.Index("ix_jobs_history_scheduled_for", "scheduled_for"),
    )
    op.add_column("jobs", sa.Column("version", sa.Integer, nullable=False, server_default="0"))


def downgrade():
    op.drop_table("jobs_history")
    op.drop_column("jobs", "version")
