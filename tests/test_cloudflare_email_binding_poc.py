import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POC = ROOT / "cloudflare-email-binding-poc"


def test_email_poc_is_local_only_and_declares_recommended_binding():
    config = json.loads((POC / "wrangler.jsonc").read_text(encoding="utf-8"))
    assert config["workers_dev"] is False
    assert config["preview_urls"] is False
    assert config["send_email"] == [{"name": "EMAIL"}]
    worker = (POC / "src" / "worker.py").read_text(encoding="utf-8")
    assert "self.env.EMAIL" not in worker
    assert "FakeEmailBinding" in worker
    assert "Promise.resolve" in worker
    assert "--remote" not in "\n".join(
        path.read_text(encoding="utf-8")
        for path in POC.rglob("*.py")
    )


def test_email_poc_uses_production_adapter_and_checks_payload():
    app = (POC / "src" / "poc_app.py").read_text(encoding="utf-8")
    worker = (POC / "src" / "worker.py").read_text(encoding="utf-8")
    adapter = (ROOT / "mail" / "cloudflare.py").read_text(encoding="utf-8")
    assert "CloudflareEmailTransport.from_request()" in app
    assert "MailAttachment" in app
    assert "base64.b64decode" in app
    assert "last_payload" in worker
    assert "pyodide.ffi.run_sync" in adapter or "_run_sync" in adapter
    assert "base64.b64encode" in adapter
    assert '"disposition": "attachment"' in adapter
