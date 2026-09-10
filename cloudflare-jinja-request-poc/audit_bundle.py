"""Verify the dry-run Worker bundle excludes runtime data, static, and secrets."""
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
ALLOWED_CONFIG = {
    "$schema",
    "name",
    "main",
    "compatibility_date",
    "compatibility_flags",
    "workers_dev",
    "preview_urls",
    "observability",
}


def audit(bundle, build=None):
    build = Path(build or ROOT / ".build")
    bundle = Path(bundle)
    config = json.loads((build / "wrangler.jsonc").read_text())
    assert config["name"] == "shuttlers-match-jinja-poc"
    assert config["workers_dev"] and not config["preview_urls"]
    assert set(config) <= ALLOWED_CONFIG
    assert not any(
        key in config
        for key in (
            "route",
            "routes",
            "assets",
            "d1_databases",
            "r2_buckets",
            "kv_namespaces",
            "durable_objects",
            "limits",
            "vars",
        )
    )
    assert not list(build.glob(".env*"))
    assert not list(build.glob(".dev.vars*"))

    approved = {
        str(path.relative_to(build / "src"))
        for path in (build / "src").rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
    files = [path for path in bundle.rglob("*") if path.is_file()]
    assert files
    forbidden_names = {"config.json", "match_state.json", "draft_state.json"}
    for path in files:
        relative = path.relative_to(bundle)
        assert path.name not in forbidden_names
        assert path.suffix != ".db"
        assert "instance" not in relative.parts
        assert "history_dumps" not in relative.parts
        assert "static" not in relative.parts
        assert not path.name.startswith((".env", ".dev.vars"))
        if relative.parts[0] != "python_modules" and str(relative) != "README.md":
            assert str(relative) in approved, str(relative)
            assert path.read_bytes() == (build / "src" / relative).read_bytes()
        payload = path.read_bytes()
        for key, value in os.environ.items():
            if any(word in key for word in ("SECRET", "TOKEN", "PASSWORD", "CREDENTIAL")):
                if len(value) >= 8:
                    assert value.encode() not in payload, "Credential match suppressed"

    result = {
        "bundle_files": len(files),
        "forbidden_files": 0,
        "credential_matches": 0,
        "python_filesystem_static_files": 0,
    }
    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / "bundle-audit.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
    return result


if __name__ == "__main__":
    print(json.dumps(audit(sys.argv[1]), indent=2))
