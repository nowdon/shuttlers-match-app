"""Fresh-process import probes, error redaction, and source bundle boundaries."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "cloudflare-import-poc" / "src"


def load_diagnostics():
    spec = importlib.util.spec_from_file_location("import_poc_diagnostics", SRC / "diagnostics.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fresh_import_has_no_runtime_effects(tmp_path):
    env = dict(os.environ)
    env.pop("SECRET_KEY", None)
    env.pop("ALLOW_DEV_SECRET_KEY", None)
    result = subprocess.run(
        [sys.executable, "-B", str(SRC / "diagnostics.py")],
        cwd=tmp_path, env=env, capture_output=True, text=True, check=True,
    )
    report = json.loads(result.stdout)
    assert report["status"] == "ok"
    assert report["app_imported"]
    assert report["app"] == {
        "blueprints": ["admin", "api", "history", "line", "match", "participant"],
        "secret_key_is_none": True, "runtime_initialized": False,
    }
    assert not any(report["blocked_attempts"].values())
    assert list(tmp_path.iterdir()) == []


def test_error_response_does_not_expose_exception_text(monkeypatch):
    module = load_diagnostics()

    def fail(name):
        raise ImportError("/private/local/path secret=do-not-publish")

    monkeypatch.setattr(module, "probe", fail)
    result = module.attempt("app")
    assert result == {
        "stage": "app", "status": "error", "exception_type": "ImportError",
        "message": "Import or timezone construction failed.",
    }


def test_probe_blocks_runtime_file_reads(monkeypatch, tmp_path):
    module = load_diagnostics()

    def forbidden_probe(name):
        (tmp_path / "config.json").open()

    monkeypatch.setattr(module, "probe", forbidden_probe)
    report = module.run_diagnostics()
    assert report["status"] == "error"
    assert report["blocked_attempts"]["runtime_file_open"] == len(module.STAGES) + 1


def test_source_links_include_only_application_python():
    for directory in ("routes", "utils", "data"):
        expected = {p.name for p in (ROOT / directory).glob("*.py")}
        actual = {p.name for p in (SRC / directory).glob("*.py")}
        assert actual == expected
        for name in expected:
            link = SRC / directory / name
            assert link.is_symlink()
            assert link.resolve() == ROOT / directory / name
    for name in ("app.py", "models.py", "logic.py"):
        assert (SRC / name).is_symlink()
        assert (SRC / name).resolve() == ROOT / name
    config = json.loads((SRC.parent / "wrangler.jsonc").read_text())
    assert config["name"] == "shuttlers-match-import-poc"
    assert not set(config) & {"routes", "d1_databases", "r2_buckets", "kv_namespaces", "durable_objects"}
