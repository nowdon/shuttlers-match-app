"""Link the production mail package into the isolated Email binding PoC."""

import os
from pathlib import Path


POC = Path(__file__).resolve().parent
ROOT = POC.parent
SOURCE = ROOT / "mail"
TARGET = POC / "src" / "mail"


def main():
    TARGET.mkdir(parents=True, exist_ok=True)
    for source in sorted(SOURCE.glob("*.py")):
        link = TARGET / source.name
        if link.is_symlink() and link.resolve() == source.resolve():
            continue
        if link.exists() or link.is_symlink():
            raise RuntimeError(f"refusing to replace {link}")
        link.symlink_to(os.path.relpath(source, link.parent))


if __name__ == "__main__":
    main()
