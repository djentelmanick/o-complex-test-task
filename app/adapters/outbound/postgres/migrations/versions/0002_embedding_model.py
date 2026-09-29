"""track which embedding model produced each vector

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-29
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Строки без модели не совпадут ни с одной настройкой и будут переиндексированы
    op.add_column(
        "knowledge_chunks",
        sa.Column("embedding_model", sa.Text(), nullable=False, server_default=""),
    )
    op.alter_column("knowledge_chunks", "embedding_model", server_default=None)


def downgrade() -> None:
    op.drop_column("knowledge_chunks", "embedding_model")
