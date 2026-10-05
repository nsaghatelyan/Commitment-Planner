"""connection auth modes: AWS profile mode, Azure client id and credential reference

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-05

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "cloud_connection",
        sa.Column("aws_auth_mode", sa.String(16), nullable=False, server_default="role"),
    )
    op.add_column("cloud_connection", sa.Column("aws_profile", sa.String(128)))
    op.add_column("cloud_connection", sa.Column("azure_client_id", sa.String(64)))
    op.add_column("cloud_connection", sa.Column("azure_credential_ref", sa.String(1024)))


def downgrade() -> None:
    op.drop_column("cloud_connection", "azure_credential_ref")
    op.drop_column("cloud_connection", "azure_client_id")
    op.drop_column("cloud_connection", "aws_profile")
    op.drop_column("cloud_connection", "aws_auth_mode")
