"""Static safety contract for the isolated Phase 7 R2 PoC."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POC = ROOT / "cloudflare-r2-binding-poc"


def test_r2_poc_is_local_only_and_uses_one_synthetic_binding():
    config = json.loads((POC / "wrangler.jsonc").read_text(encoding="utf-8"))
    assert config["workers_dev"] is False
    assert config["preview_urls"] is False
    assert config["r2_buckets"] == [{
        "binding": "HISTORY_ARCHIVES",
        "bucket_name": "shuttlers-match-r2-poc-local",
    }]
    text = "\n".join(path.read_text(encoding="utf-8") for path in POC.rglob("*.py"))
    assert "--remote" not in text
    assert "production" not in config["r2_buckets"][0]["bucket_name"]


def test_r2_poc_exercises_bytes_metadata_pagination_and_all_primitives():
    worker = (POC / "src/worker.py").read_text(encoding="utf-8")
    app = (POC / "src/poc_app.py").read_text(encoding="utf-8")
    adapter = (ROOT / "storage/history_archives.py").read_text(encoding="utf-8")
    assert "wsgi.fetch(application" in worker
    assert "R2HistoryArchiveStorage" in app
    assert "list_limit=1" in app
    for operation in (
        "storage.put_history_archive",
        "storage.get_history_archive",
        "storage.list_history_archives",
        "storage.delete_history_archive",
    ):
        assert operation in app
    assert "run_sync(binding.get" in app
    assert "stored.arrayBuffer()" in adapter
    assert "page.truncated" in adapter
    assert 'getattr(page, "cursor"' in adapter
    assert "item.size" in adapter
    assert "item.uploaded" in adapter
