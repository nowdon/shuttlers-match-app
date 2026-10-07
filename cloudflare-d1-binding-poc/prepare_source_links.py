"""Create links to the current Flask app sources without copying runtime data."""
from pathlib import Path


POC = Path(__file__).resolve().parent
ROOT = POC.parent
SRC = POC / "src"
SOURCES = (
    Path("app.py"),
    Path("models.py"),
    Path("logic.py"),
    *(path.relative_to(ROOT) for directory in ("data", "routes", "utils")
      for path in sorted((ROOT / directory).glob("*.py"))),
)


def main():
    for relative in SOURCES:
        link = SRC / relative
        link.parent.mkdir(parents=True, exist_ok=True)
        target = Path("../" * len(link.relative_to(POC).parents)) / relative
        if link.is_symlink() and link.resolve() == (ROOT / relative).resolve():
            continue
        if link.exists() or link.is_symlink():
            raise RuntimeError(f"refusing to replace {link.relative_to(POC)}")
        link.symlink_to(target)


if __name__ == "__main__":
    main()
