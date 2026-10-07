"""History archive mapping, manifests, and safe local/R2 copy operations."""

import hashlib
from pathlib import Path
import re
import subprocess

from storage.history_archives import R2HistoryArchiveStorage

from .errors import MigrationError, ValidationError
from .manifest import sha256_file, write_json


HISTORY_DUMP_FILENAME_RE = re.compile(
    r"^match_history_[A-Za-z0-9_]+_\d{8}_\d{6}_\d{6}\.json$"
)


def archive_key_for_filename(filename):
    """Reuse the production R2 adapter's filename-to-key mapping."""
    if not HISTORY_DUMP_FILENAME_RE.fullmatch(str(filename or "")):
        raise MigrationError(f"Unsupported history archive filename: {filename}")
    return R2HistoryArchiveStorage.key_for_filename(filename)


def build_archive_plan(directory, output=None, *, dry_run=False):
    directory = Path(directory).resolve()
    if not directory.is_dir():
        raise MigrationError(f"History archive directory does not exist: {directory}")
    archives = []
    ignored_files = []
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        if not path.is_file():
            continue
        if not HISTORY_DUMP_FILENAME_RE.fullmatch(path.name):
            ignored_files.append({
                "filename": path.name,
                "source_path": str(path),
                "reason": "not_history_archive_filename",
            })
            continue
        archives.append({
            "filename": path.name,
            "source_path": str(path),
            "target_key": archive_key_for_filename(path.name),
            "size": path.stat().st_size,
            "sha256": sha256_file(path),
        })
    plan = {
        "format_version": 1,
        "source_directory": str(directory),
        "archives": archives,
        "ignored_files": ignored_files,
        "object_count": len(archives),
    }
    validate_archive_plan(plan)
    if output is not None and not dry_run:
        write_json(output, plan)
    return plan


def validate_archive_plan(plan, *, verify_sources=True):
    """Validate archive plan shape, mapping, and optional source hashes."""
    if not isinstance(plan, dict) or plan.get("format_version") != 1:
        raise ValidationError("Unsupported archive plan")
    archives = plan.get("archives")
    ignored_files = plan.get("ignored_files")
    if not isinstance(archives, list) or not isinstance(ignored_files, list):
        raise ValidationError("Archive plan lists are invalid")
    if plan.get("object_count") != len(archives):
        raise ValidationError("Archive plan object count is invalid")
    seen_filenames = set()
    seen_keys = set()
    for item in archives:
        if not isinstance(item, dict):
            raise ValidationError("Archive plan entry is invalid")
        required = {"filename", "source_path", "target_key", "size", "sha256"}
        if set(item) != required:
            raise ValidationError("Archive plan entry shape is invalid")
        filename = item["filename"]
        target_key = item["target_key"]
        if filename in seen_filenames or target_key in seen_keys:
            raise ValidationError("Archive plan contains duplicate entries")
        seen_filenames.add(filename)
        seen_keys.add(target_key)
        if target_key != archive_key_for_filename(filename):
            raise ValidationError(f"Archive key mapping is invalid: {filename}")
        if not isinstance(item["size"], int) or item["size"] < 0:
            raise ValidationError(f"Archive size is invalid: {filename}")
        if not isinstance(item["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
            raise ValidationError(f"Archive checksum is invalid: {filename}")
        if verify_sources:
            source = Path(item["source_path"])
            if not source.is_file():
                raise ValidationError(f"Archive source does not exist: {filename}")
            if source.stat().st_size != item["size"] or sha256_file(source) != item["sha256"]:
                raise ValidationError(f"Archive source checksum mismatch: {filename}")
    return plan


def _same_file_bytes(path, data):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest() == hashlib.sha256(data).hexdigest()


def copy_archives_to_directory(plan, target_directory):
    """A deterministic local object-store stand-in used by unit tests."""
    target_directory = Path(target_directory).resolve()
    target_directory.mkdir(parents=True, exist_ok=True)
    results = []
    for item in plan["archives"]:
        source = Path(item["source_path"])
        target = target_directory / item["target_key"]
        target.parent.mkdir(parents=True, exist_ok=True)
        data = source.read_bytes()
        if target.exists():
            if not _same_file_bytes(target, data):
                raise ValidationError(f"R2 target key differs: {item['target_key']}")
            status = "idempotent_existing"
        else:
            target.write_bytes(data)
            status = "uploaded"
        results.append({"key": item["target_key"], "status": status})
    return results


def _run_wrangler(wrangler, args, *, cwd=None):
    command = [str(wrangler), *args]
    return subprocess.run(command, cwd=cwd, check=True, capture_output=True, text=True)


def upload_archives_with_wrangler(
    plan,
    *,
    wrangler,
    bucket,
    persist_to,
    config=None,
):
    """Upload only to Wrangler's explicitly local R2 store.

    Existing objects are downloaded and compared before any write.  The
    ``--force`` option is deliberately never used.
    """
    persist_to = Path(persist_to).resolve()
    config_args = ["--config", str(config)] if config else []
    results = []
    for index, item in enumerate(plan["archives"]):
        source = Path(item["source_path"])
        object_path = f"{bucket}/{item['target_key']}"
        existing = persist_to / f"r2-existing-{index}.bin"
        get = subprocess.run(
            [str(wrangler), "r2", "object", "get", object_path, "--local",
             "--persist-to", str(persist_to), *config_args, "--file", str(existing)],
            capture_output=True,
            text=True,
        )
        if get.returncode == 0:
            try:
                same = _same_file_bytes(existing, source.read_bytes())
            finally:
                existing.unlink(missing_ok=True)
            if not same:
                raise ValidationError(f"R2 target key differs: {item['target_key']}")
            results.append({"key": item["target_key"], "status": "idempotent_existing"})
            continue
        existing.unlink(missing_ok=True)
        _run_wrangler(
            wrangler,
            ["r2", "object", "put", object_path, "--local", "--persist-to",
             str(persist_to), *config_args, "--file", str(source),
             "--content-type", "application/json"],
        )
        results.append({"key": item["target_key"], "status": "uploaded"})
    return results


def validate_archive_directory(plan, target_directory):
    target_directory = Path(target_directory).resolve()
    expected = {item["target_key"]: item for item in plan["archives"]}
    actual = {
        str(path.relative_to(target_directory)): path
        for path in target_directory.rglob("*") if path.is_file()
    } if target_directory.is_dir() else {}
    if set(actual) != set(expected):
        raise ValidationError("R2 object key set does not match archive plan")
    for key, item in expected.items():
        target = actual[key]
        if target.stat().st_size != item["size"] or sha256_file(target) != item["sha256"]:
            raise ValidationError(f"R2 archive checksum mismatch: {key}")
    return {"object_count": len(actual), "keys": sorted(actual)}
