"""Reusable helpers for Synth Gallery Alembic revisions.

All helpers operate on the SQLAlchemy Connection that Alembic provides via
``op.get_bind()``. SQLite specifics:

- CHECK constraints and column types can only be changed by rebuilding the
  table (create shadow, copy, drop, rename).
- ``PRAGMA foreign_keys`` is a no-op inside an open transaction, so every
  rebuild commits first and toggles the pragma around the DDL — otherwise
  ``DROP TABLE`` cascades into child rows and silently destroys them.
"""
from pathlib import Path


def column_exists(conn, table: str, column: str) -> bool:
    """Check whether a column exists in a table."""
    rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").mappings().all()
    return any(row["name"] == column for row in rows)


def table_exists(conn, table: str) -> bool:
    """Check whether a table exists in the database."""
    row = conn.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).first()
    return row is not None


def backup_database(db_path: Path, suffix: str) -> Path | None:
    """Copy the database file to ``<db_path><suffix>`` before a breaking change.

    Best-effort safety net; failures are logged but do not abort the
    migration. Returns None when the source does not exist.
    """
    import logging
    import shutil

    src = str(db_path)
    if not db_path.exists():
        return None
    dest = Path(f"{src}{suffix}")
    try:
        shutil.copy2(src, dest)
    except OSError as exc:
        logging.getLogger(__name__).warning(
            "Pre-migration backup of %s failed: %s", src, exc
        )
        return None
    return dest


def rebuild_table(conn, table: str, create_ddl: str, keep_columns: list[str]) -> None:
    """Rebuild ``table`` with ``create_ddl``, copying ``keep_columns`` over.

    SQLite cannot add/drop CHECK constraints or alter a column type in
    place, so changing a table's shape requires a full rebuild: create a
    shadow table with the new schema, copy the surviving columns, drop the
    original and rename. ``create_ddl`` is the full ``CREATE TABLE``
    statement for the new shape; ``keep_columns`` lists columns that exist
    in both old and new tables and must be preserved.
    """
    # PRAGMA foreign_keys is silently ignored inside an open transaction,
    # and the preceding data-fixing UPDATE usually left one open. Commit
    # first, otherwise DROP TABLE cascades into child tables (album_items,
    # item_media, ...) and silently destroys their rows.
    conn.commit()
    conn.exec_driver_sql("PRAGMA foreign_keys = OFF")
    new_table = f"{table}_new"
    conn.exec_driver_sql(f"DROP TABLE IF EXISTS {new_table}")
    conn.exec_driver_sql(create_ddl.replace(table, new_table, 1))

    quoted = ", ".join(f'"{c}"' for c in keep_columns)
    conn.exec_driver_sql(
        f'INSERT INTO {new_table} ({quoted}) SELECT {quoted} FROM {table}'
    )

    conn.exec_driver_sql(f"DROP TABLE {table}")
    conn.exec_driver_sql(f"ALTER TABLE {new_table} RENAME TO {table}")
    conn.exec_driver_sql("PRAGMA foreign_keys = ON")


def recreate_table_without_column(conn, table: str, column: str) -> None:
    """Recreate a table without the given column.

    SQLite cannot drop a column that is referenced by a table-level foreign
    key constraint, so we build the new schema dynamically from PRAGMA
    output and copy the surviving data over.
    """
    conn.commit()
    conn.exec_driver_sql("PRAGMA foreign_keys = OFF")

    columns = [
        row for row in conn.exec_driver_sql(f"PRAGMA table_info({table})").mappings().all()
        if row["name"] != column
    ]
    column_names = [c["name"] for c in columns]

    # Build column definitions from PRAGMA output.
    col_defs = []
    for col in columns:
        parts = [f'"{col["name"]}"', col["type"]]
        if col["notnull"]:
            parts.append("NOT NULL")
        if col["dflt_value"] is not None:
            parts.append(f"DEFAULT ({col['dflt_value']})")
        if col["pk"]:
            parts.append("PRIMARY KEY")
        col_defs.append(" ".join(parts))

    # Keep foreign keys that do not involve the dropped column.
    fks = [
        row for row in conn.exec_driver_sql(f"PRAGMA foreign_key_list({table})").mappings().all()
        if row["from"] != column and row["to"] != column
    ]
    # Group by constraint id so multi-column FKs stay intact.
    fk_groups: dict[int, list] = {}
    for fk in fks:
        fk_groups.setdefault(fk["id"], []).append(fk)
    for group in fk_groups.values():
        from_cols = ", ".join(f'"{fk["from"]}"' for fk in group)
        to_table = group[0]["table"]
        to_cols = ", ".join(f'"{fk["to"]}"' for fk in group)
        on_update = group[0]["on_update"]
        on_delete = group[0]["on_delete"]
        clause = f"FOREIGN KEY ({from_cols}) REFERENCES {to_table} ({to_cols})"
        if on_update and on_update != "NO ACTION":
            clause += f" ON UPDATE {on_update}"
        if on_delete and on_delete != "NO ACTION":
            clause += f" ON DELETE {on_delete}"
        col_defs.append(clause)

    new_table = f"{table}_new"
    conn.exec_driver_sql(f"DROP TABLE IF EXISTS {new_table}")
    conn.exec_driver_sql(f"CREATE TABLE {new_table} ({', '.join(col_defs)})")

    quoted_names = ", ".join(f'"{name}"' for name in column_names)
    conn.exec_driver_sql(
        f"INSERT INTO {new_table} ({quoted_names}) SELECT {quoted_names} FROM {table}"
    )

    conn.exec_driver_sql(f"DROP TABLE {table}")
    conn.exec_driver_sql(f"ALTER TABLE {new_table} RENAME TO {table}")

    conn.exec_driver_sql("PRAGMA foreign_keys = ON")
