"""Albums hold media only: remove note memberships and note covers.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-11

Adding notes to albums was possible via drag-and-drop, but album cards
render a thumbnail of their first (or explicit cover) item and notes
without a cover image have none - the failed thumbnail request painted
an "Access denied" placeholder on the album card and locked it. Albums
are now media-only at the service layer; this revision cleans up data
created before the guard existed:

- ``album_items`` rows pointing at notes are deleted
- ``albums.cover_item_id`` values pointing at notes are cleared
  (the card falls back to its first media item, or "Empty Album")

Albums that contained only notes become empty albums.
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql(
        "DELETE FROM album_items WHERE item_id IN "
        "(SELECT id FROM items WHERE type = 'note')"
    )
    conn.exec_driver_sql(
        "UPDATE albums SET cover_item_id = NULL WHERE cover_item_id IN "
        "(SELECT id FROM items WHERE type = 'note')"
    )


def downgrade() -> None:
    # Data cleanup is not reversible: the removed memberships and cleared
    # covers cannot be reconstructed.
    pass
