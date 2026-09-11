"""Dry-run, audit, then deploy only the dedicated workers.dev Worker."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile

from audit_bundle import audit


ROOT = Path(__file__).resolve().parent
BASE = "https://shuttlers-match-runtime-poc.nowdon.workers.dev"


def cli(pywrangler, arguments, log_name):
    log_path = Path("/tmp") / log_name
    with log_path.open("w") as output:
        subprocess.run(
            [pywrangler, *arguments],
            cwd=ROOT / ".build",
            stdout=output,
            stderr=subprocess.STDOUT,
            check=True,
        )
    return log_path.read_text()


def deploy(pywrangler, label):
    bundle = Path(tempfile.mkdtemp(prefix="runtime-sqlite-bundle-"))
    dry = cli(
        pywrangler,
        ["deploy", "--dry-run", "--outdir", str(bundle)],
        f"runtime-sqlite-{label}-dry.log",
    )
    audit_result = audit(bundle)
    deployed = cli(pywrangler, ["deploy"], f"runtime-sqlite-{label}-deploy.log")
    size = re.search(r"Total Upload: ([\d.]+) KiB / gzip: ([\d.]+) KiB", deployed)
    modules = re.search(r"Total \((\d+) modules\)", dry)
    startup = re.search(r"Worker Startup Time: (\d+) ms", deployed)
    version = re.search(r"Current Version ID: ([a-f0-9-]+)", deployed)
    assert size and version, deployed
    metadata = {
        "worker": "shuttlers-match-runtime-poc",
        "workers_dev_url": BASE,
        "module_count": int(modules.group(1)) if modules else None,
        "uncompressed_kib": float(size.group(1)),
        "gzip_kib": float(size.group(2)),
        "startup_ms": int(startup.group(1)) if startup else None,
        "version_id": version.group(1),
        "bundle_audit": audit_result,
    }
    destination = ROOT / "results" / f"deploy-{label}.json"
    destination.write_text(json.dumps(metadata, indent=2) + "\n")
    return metadata


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pywrangler", required=True)
    parser.add_argument("--label", choices=("first", "second"), required=True)
    args = parser.parse_args()
    print(json.dumps(deploy(args.pywrangler, args.label), indent=2), flush=True)
