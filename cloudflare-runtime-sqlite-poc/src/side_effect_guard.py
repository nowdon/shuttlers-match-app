"""Permanent PoC boundary: only the selected disposable SQLite file may be used."""
import os
from pathlib import Path
import sys


DB_SUFFIXES = (".db", ".db-wal", ".db-shm", ".db-journal")
STATE_NAMES = {"config.json", "match_state.json", "draft_state.json"}
_phase = "import"
_expected_db = None
_allowed_database_files = set()


class PocBoundaryViolation(RuntimeError):
    pass


def _normal(path):
    return Path(os.path.abspath(os.fsdecode(path))).resolve(strict=False)


def _is_within(path, parent):
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def configure_runtime(expected_db):
    global _phase, _expected_db, _allowed_database_files
    expected = _normal(expected_db)
    _expected_db = expected
    _allowed_database_files = {
        expected,
        Path(str(expected) + "-wal"),
        Path(str(expected) + "-shm"),
        Path(str(expected) + "-journal"),
    }
    _phase = "runtime"


def expected_database():
    return _expected_db


def _reject(reason):
    raise PocBoundaryViolation(f"PoC side-effect boundary rejected {reason}")


def _check_path(path):
    normalized = _normal(path)
    text = normalized.as_posix()
    name = normalized.name
    if name in STATE_NAMES or "history_dumps" in normalized.parts:
        _reject("runtime data access")
    if name.endswith(DB_SUFFIXES) and normalized not in _allowed_database_files:
        _reject("non-disposable SQLite access")
    return normalized


def audit(event, args):
    if event == "socket.connect":
        _reject("network access")
    if event == "sqlite3.connect":
        if _phase == "import":
            _reject("SQLite during import")
        database = args[0]
        if not isinstance(database, (str, bytes, os.PathLike)):
            _reject("unknown SQLite target")
        if _normal(database) != _expected_db:
            _reject("non-disposable SQLite connection")
    if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
        _check_path(args[0])


sys.addaudithook(audit)
