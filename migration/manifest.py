"""Deterministic serialization and metadata helpers for migration artifacts."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets


def canonical_json_bytes(value):
    """Return the one canonical JSON representation used for checksums."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_ndjson_bytes(rows):
    """Encode rows in deterministic newline-delimited JSON form."""
    if not rows:
        return b""
    return b"".join(canonical_json_bytes(row) + b"\n" for row in rows)


def sha256_bytes(value):
    return hashlib.sha256(bytes(value)).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now_text():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def new_migration_id():
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{secrets.token_hex(4)}"


def table_summary(rows, primary_key="id"):
    """Return safe metadata for a canonical list of row dictionaries."""
    ordered_rows = list(rows)
    ids = [row.get(primary_key) for row in ordered_rows]
    ids = [value for value in ids if value is not None]
    return {
        "row_count": len(ordered_rows),
        "min_id": min(ids) if ids else None,
        "max_id": max(ids) if ids else None,
        "sha256": sha256_bytes(canonical_ndjson_bytes(ordered_rows)),
    }


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Invalid JSON artifact: {path}") from error
