"""SQLite implementation of the common storage execution contract."""

from pathlib import Path
import sqlite3

from storage.errors import normalize_storage_error
from storage.result import StorageResult


class SQLiteStorage:
    """Execute prepared SQLite statements against one lazily opened connection.

    The connection belongs to this adapter instance. The request provider closes
    it during request teardown; explicitly created adapters should use ``close``.
    """

    def __init__(self, database_path):
        self.database_path = Path(database_path)
        self._connection = None

    def _get_connection(self):
        if self._connection is None:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(self.database_path)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
        return self._connection

    @staticmethod
    def _rows(cursor):
        if cursor.description is None:
            return []
        return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def _result(cursor, rows, changes):
        return StorageResult(
            rows=rows,
            changes=changes,
            last_row_id=cursor.lastrowid,
        )

    def run(self, sql, *params):
        connection = self._get_connection()
        before_changes = connection.total_changes
        try:
            cursor = connection.execute(sql, params)
            rows = self._rows(cursor)
            changes = connection.total_changes - before_changes
            connection.commit()
            return self._result(cursor, rows, changes)
        except sqlite3.Error as error:
            connection.rollback()
            raise normalize_storage_error(error) from None

    def first(self, sql, *params):
        connection = self._get_connection()
        try:
            row = connection.execute(sql, params).fetchone()
            return dict(row) if row is not None else None
        except sqlite3.Error as error:
            raise normalize_storage_error(error) from None

    def all(self, sql, *params):
        connection = self._get_connection()
        try:
            return [dict(row) for row in connection.execute(sql, params).fetchall()]
        except sqlite3.Error as error:
            raise normalize_storage_error(error) from None

    def batch(self, statements):
        connection = self._get_connection()
        results = []
        try:
            connection.execute("BEGIN")
            for sql, params in statements:
                before_changes = connection.total_changes
                cursor = connection.execute(sql, tuple(params))
                rows = self._rows(cursor)
                changes = connection.total_changes - before_changes
                results.append(self._result(cursor, rows, changes))
            connection.commit()
            return results
        except sqlite3.Error as error:
            connection.rollback()
            raise normalize_storage_error(error) from None

    def close(self):
        if self._connection is not None:
            self._connection.close()
            self._connection = None
