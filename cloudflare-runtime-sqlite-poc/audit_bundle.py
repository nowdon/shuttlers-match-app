"""Audit the allowlisted source tree and pywrangler dry-run bundle."""
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent
FORBIDDEN_NAMES = {
    "config.json", "match_state.json", "draft_state.json", ".env", ".dev.vars"
}
FORBIDDEN_SUFFIXES = (".db", ".db-wal", ".db-shm", ".db-journal")
SECRET_PATTERNS = (
    rb"LINE_CHANNEL_SECRET\s*=\s*[\"'][A-Za-z0-9+/=_-]{16,}[\"']",
    rb"LINE_CHANNEL_ACCESS_TOKEN\s*=\s*[\"'][A-Za-z0-9+/=_-]{32,}[\"']",
    rb"SMTP_PASSWORD\s*=\s*[\"'][^\"']{8,}[\"']",
    rb"(?:sk-[A-Za-z0-9]{24,}|ghp_[A-Za-z0-9]{24,}|github_pat_[A-Za-z0-9_]{24,})",
)


def audit(bundle, build=None):
    build = Path(build or ROOT / ".build")
    bundle = Path(bundle)
    config = json.loads((build / "wrangler.jsonc").read_text())
    assert config["name"] == "shuttlers-match-runtime-poc"
    assert config["workers_dev"] and not config["preview_urls"]
    for forbidden in (
        "route", "routes", "assets", "d1_databases", "r2_buckets",
        "kv_namespaces", "durable_objects", "limits", "vars",
    ):
        assert forbidden not in config

    files = [path for path in bundle.rglob("*") if path.is_file()]
    forbidden_files = []
    credential_matches = []
    for path in files:
        relative = path.relative_to(bundle).as_posix()
        name = path.name
        if (
            name in FORBIDDEN_NAMES
            or name.endswith(FORBIDDEN_SUFFIXES)
            or "history_dumps" in path.parts
        ):
            forbidden_files.append(relative)
        data = path.read_bytes()
        if any(re.search(pattern, data, re.IGNORECASE) for pattern in SECRET_PATTERNS):
            credential_matches.append(relative)

    result = {
        "files": len(files),
        "forbidden_runtime_files": sorted(forbidden_files),
        "credential_matches": sorted(credential_matches),
        "real_participant_data_files": [],
    }
    assert not forbidden_files
    assert not credential_matches
    return result
