"""Exercise the synthetic Phase 9 Email binding PoC with local Wrangler."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.request import Request, urlopen


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: run_phase9_wrangler_local.py /path/to/wrangler")
    wrangler = str(Path(sys.argv[1]).resolve())
    version = subprocess.run(
        [wrangler, "--version"], check=True, capture_output=True, text=True,
        env=dict(os.environ, WRANGLER_LOG_PATH=os.devnull),
    ).stdout
    if "4.131.1" not in version:
        raise AssertionError(f"expected Wrangler 4.131.1, got {version.strip()}")

    repository = Path(__file__).resolve().parents[1]
    poc = repository / "cloudflare-email-binding-poc"
    subprocess.run(
        [sys.executable, str(poc / "prepare_source_links.py")],
        check=True,
        cwd=repository,
    )
    python_modules = poc / "python_modules"
    if not python_modules.exists():
        for candidate in (
            repository / "cloudflare-d1-binding-poc" / "python_modules",
            repository / "cloudflare-wsgi-poc" / "python_modules",
            repository / "cloudflare-r2-binding-poc" / "python_modules",
        ):
            if candidate.is_dir():
                python_modules.symlink_to(candidate, target_is_directory=True)
                break
    with tempfile.TemporaryDirectory(prefix="phase9-email-") as temporary:
        temp = Path(temporary)
        state = temp / "state"
        log = temp / "wrangler.log"
        environment = dict(
            os.environ,
            CI="1",
            NO_COLOR="1",
            WRANGLER_LOG_PATH=str(log),
        )
        command = [
            wrangler, "dev", "--local", "--persist-to", str(state),
            "--config", str(poc / "wrangler.jsonc"), "--port", "18792",
        ]
        server = subprocess.Popen(
            command, cwd=poc, env=environment, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True,
        )
        result = None
        try:
            for _ in range(90):
                try:
                    with urlopen(
                        Request(
                            "http://127.0.0.1:18792/email-poc/send",
                            data=b"",
                            method="POST",
                        ),
                        timeout=5,
                    ) as response:
                        result = json.load(response)
                    break
                except Exception:
                    if server.poll() is not None:
                        break
                    time.sleep(1)
            if server.poll() is not None or result is None:
                output = server.stdout.read() if server.stdout else ""
                raise AssertionError(
                    "wrangler dev did not complete the Email probe\n" + output
                )
            assert result["ok"] is True
            assert result["adapter"] == "CloudflareEmailTransport"
            assert result["binding"] == "EMAIL"
            assert result["run_sync"] is True
            assert result["real_email_service_used"] is False
            assert result["message_id"] == "phase9-fake-message-id"
            assert result["to"] == "poc-recipient@example.com"
            assert result["from"] == {
                "email": "poc-sender@example.com",
                "name": "Phase 9 PoC",
            }
            assert result["subject"] == "Phase 9 synthetic email"
            assert result["attachment"] == {
                "filename": "match_history_phase9.json",
                "type": "application/json",
                "disposition": "attachment",
                "base64_bytes_match": True,
            }
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=10)
    print("Phase 9 Wrangler local Email checks passed")


if __name__ == "__main__":
    main()
