"""Exercise synthetic history archive bytes on disposable Wrangler local R2."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.request import Request, urlopen


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: run_phase7_wrangler_local.py /path/to/wrangler")
    wrangler = str(Path(sys.argv[1]).resolve())
    version = subprocess.run(
        [wrangler, "--version"], check=True, capture_output=True, text=True,
        env=dict(os.environ, WRANGLER_LOG_PATH=os.devnull),
    ).stdout
    if "4.131.1" not in version:
        raise AssertionError(f"expected Wrangler 4.131.1, got {version.strip()}")

    repository = Path(__file__).resolve().parents[1]
    poc = repository / "cloudflare-r2-binding-poc"
    subprocess.run(
        [sys.executable, str(poc / "prepare_source_links.py")],
        check=True,
        cwd=repository,
    )
    with tempfile.TemporaryDirectory(prefix="phase7-r2-") as temporary:
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
            "--config", str(poc / "wrangler.jsonc"), "--port", "18791",
        ]
        server = subprocess.Popen(
            command, cwd=poc, env=environment, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True,
        )
        output = ""
        try:
            for _ in range(90):
                try:
                    with urlopen(
                        Request("http://127.0.0.1:18791/exercise", data=b"", method="POST"),
                        timeout=5,
                    ) as response:
                        result = json.load(response)
                    break
                except Exception:
                    if server.poll() is not None:
                        break
                    time.sleep(1)
            else:
                result = None
            if server.poll() is not None or result is None:
                output = server.stdout.read() if server.stdout else ""
                raise AssertionError("wrangler dev did not complete the R2 probe\n" + output)
            assert result["ok"] is True
            assert result["adapter"] == "R2HistoryArchiveStorage"
            assert result["binding"] == "HISTORY_ARCHIVES"
            assert result["run_sync"] is True
            assert result["bytes_round_trip"] is True
            assert result["content_type"] == "application/json; charset=utf-8"
            assert result["listed"] == 2
            assert result["pagination_limit"] == 1
            assert result["sizes"] == [32, 34]
            uploaded = [datetime.fromisoformat(value) for value in result["uploaded_iso"]]
            assert all(value.utcoffset() == timezone.utc.utcoffset(None)
                       for value in uploaded)
            assert result["deleted"] is True
            assert result["single_key_delete"] is True
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=10)
    print("Phase 7 Wrangler local R2 checks passed")


if __name__ == "__main__":
    main()
