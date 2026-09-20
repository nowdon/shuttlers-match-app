"""Consistent, read-only SQLite snapshots for migration work."""

from contextlib import closing
import sqlite3
from pathlib import Path

from .errors import MigrationError
from .manifest import sha256_file, utc_now_text, write_json
from .schema import existing_tables, sqlite_user_version


def open_readonly(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise MigrationError(f"SQLite source does not exist: {path}")
    try:
        connection = sqlite3.connect(
            f"file:{path}?mode=ro", uri=True, check_same_thread=False
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        return connection
    except sqlite3.Error as error:
        raise MigrationError(f"Unable to open SQLite source read-only: {path}") from error


def _snapshot_metadata(path):
    path = Path(path)
    with closing(open_readonly(path)) as connection:
        tables = sorted(existing_tables(connection))
        row_counts = {
            table: int(connection.execute(
                f'SELECT COUNT(*) FROM "{table.replace(chr(34), chr(34) * 2)}"'
            ).fetchone()[0])
            for table in tables
        }
        return {
            "filename": path.name,
            "size": path.stat().st_size,
            "sha256": sha256_file(path),
            "sqlite_user_version": sqlite_user_version(connection),
            "tables": tables,
            "row_counts": row_counts,
        }


def create_snapshot(source, output, *, manifest_path=None):
    """Use SQLite's backup API instead of copying a live database file.

    The source is opened using ``mode=ro`` and ``query_only``.  SQLite's
    backup API reads a consistent view even when the source is in WAL mode.
    The destination must not already exist, which prevents accidental
    replacement of an earlier artifact.
    """
    source = Path(source).resolve()
    output = Path(output).resolve()
    if source == output:
        raise MigrationError("Snapshot output must differ from the source database")
    if output.exists():
        raise MigrationError(f"Snapshot output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)

    source_hash_before = sha256_file(source)
    try:
        source_connection = open_readonly(source)
        destination = sqlite3.connect(output)
        try:
            source_connection.backup(destination)
            destination.commit()
        finally:
            destination.close()
            source_connection.close()
    except (OSError, sqlite3.Error) as error:
        output.unlink(missing_ok=True)
        raise MigrationError("SQLite backup snapshot failed") from error

    metadata = _snapshot_metadata(output)
    source_hash_after = sha256_file(source)
    metadata = {
        "created_at": utc_now_text(),
        "source_filename": source.name,
        "source_path": str(source),
        "source_sha256": source_hash_before,
        "source_sha256_after": source_hash_after,
        "source_changed_during_snapshot": source_hash_before != source_hash_after,
        "snapshot_sha256": metadata["sha256"],
        "snapshot": metadata,
    }
    if manifest_path is not None:
        write_json(manifest_path, metadata)
    return metadata


def snapshot_metadata(snapshot):
    """Read metadata from an already-created snapshot without changing it."""
    path = Path(snapshot).resolve()
    metadata = _snapshot_metadata(path)
    return {
        "source_filename": path.name,
        "source_sha256": metadata["sha256"],
        "snapshot_sha256": metadata["sha256"],
        "snapshot": metadata,
    }
