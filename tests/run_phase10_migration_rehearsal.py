"""Run the Phase 10 synthetic local D1/R2 rehearsal."""

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from migration.rehearsal import run_rehearsal


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("wrangler", help="Wrangler 4.131.1 binary")
    parser.add_argument("--workdir", type=Path)
    args = parser.parse_args()
    report = run_rehearsal(args.wrangler, args.workdir)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
