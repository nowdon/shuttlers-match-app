"""Dry-run, audit, then deploy only the dedicated workers.dev Worker."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile

from audit_bundle import audit


ROOT = Path(__file__).resolve().parent
BASE = "https://shuttlers-match-jinja-poc.nowdon.workers.dev"


def cli(uv, arguments, log_name):
    log_path = Path("/tmp") / log_name
    with log_path.open("w") as output:
        subprocess.run(
            [uv, "run", "--directory", str(ROOT / ".build"), "pywrangler", *arguments],
            stdout=output,
            stderr=subprocess.STDOUT,
            check=True,
        )
    return log_path.read_text()


def deploy(uv):
    bundle = Path(tempfile.mkdtemp(prefix="jinja-request-bundle-"))
    dry = cli(uv, ["deploy", "--dry-run", "--outdir", str(bundle)], "jinja-request-dry.log")
    audit_result = audit(bundle)
    deployed = cli(uv, ["deploy"], "jinja-request-deploy.log")
    size = re.search(r"Total Upload: ([\d.]+) KiB / gzip: ([\d.]+) KiB", deployed)
    modules = re.search(r"Total \((\d+) modules\)", dry)
    startup = re.search(r"Worker Startup Time: (\d+) ms", deployed)
    version = re.search(r"Current Version ID: ([a-f0-9-]+)", deployed)
    assert size and version, deployed
    metadata = {
        "worker": "shuttlers-match-jinja-poc",
        "workers_dev_url": BASE,
        "module_count": int(modules.group(1)) if modules else None,
        "uncompressed_kib": float(size.group(1)),
        "gzip_kib": float(size.group(2)),
        "startup_ms": int(startup.group(1)) if startup else None,
        "version_id": version.group(1),
        "bundle_audit": audit_result,
    }
    (ROOT / "results" / "deploy.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return metadata


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--uv", default="uv")
    args = parser.parse_args()
    print(json.dumps(deploy(args.uv), indent=2), flush=True)
