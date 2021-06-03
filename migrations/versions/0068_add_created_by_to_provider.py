"""empty message

Revision ID: 0068_add_created_by_to_provider
Revises: 0067_service_contact_block
Create Date: 2017-03-06 17:19:28.492005

"""

# revision identifiers, used by Alembic.
revision = "0068_add_created_by_to_provider"
down_revision = "0067_service_contact_block"

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column(
        "provider_details",
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        op.f("ix_provider_details_created_by_id"),
        "provider_details",
        ["created_by_id"],
        unique=False,
    )
    op.create_foreign_key(
        "provider_details_created_by_id_fkey",
        "provider_details",
        "users",
        ["created_by_id"],
        ["id"],
    )
    op.add_column(
        "provider_details_history",
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        op.f("ix_provider_details_history_created_by_id"),
        "provider_details_history",
        ["created_by_id"],
        unique=False,
    )
    op.create_foreign_key(
        "provider_details_history_created_by_id_fkey",
        "provider_details_history",
        "users",
        ["created_by_id"],
        ["id"],
    )
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_constraint(
        "provider_details_history_created_by_id_fkey",
        "provider_details_history",
        type_="foreignkey",
    )
    op.drop_index(
        op.f("ix_provider_details_history_created_by_id"),
        table_name="provider_details_history",
    )
    op.drop_column("provider_details_history", "created_by_id")
    op.drop_constraint("provider_details_created_by_id_fkey", "provider_details", type_="foreignkey")
    op.drop_index(op.f("ix_provider_details_created_by_id"), table_name="provider_details")
    op.drop_column("provider_details", "created_by_id")
    # ### end Alembic commands ###
