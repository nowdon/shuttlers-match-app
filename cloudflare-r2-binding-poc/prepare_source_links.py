"""Link the production archive adapter into the isolated local R2 PoC."""

from pathlib import Path


POC = Path(__file__).resolve().parent
ROOT = POC.parent
PACKAGE = POC / "src" / "storage"
SOURCE = Path("storage/history_archives.py")


def main():
    # Wrangler follows file symlinks as Python modules, but does not traverse a
    # symlinked package directory. Migrate only the old generated directory link.
    if PACKAGE.is_symlink() and PACKAGE.resolve() == (ROOT / "storage").resolve():
        PACKAGE.unlink()
    PACKAGE.mkdir(parents=True, exist_ok=True)
    for generated_link in PACKAGE.glob("*.py"):
        if (generated_link.is_symlink()
                and generated_link.resolve().parent == (ROOT / "storage").resolve()
                and generated_link.name != SOURCE.name):
            generated_link.unlink()

    link = POC / "src" / SOURCE
    target = Path("../" * len(link.relative_to(POC).parents)) / SOURCE
    if link.is_symlink() and link.resolve() == (ROOT / SOURCE).resolve():
        return
    if link.exists() or link.is_symlink():
        raise RuntimeError(f"refusing to replace {link.relative_to(POC)}")
    link.symlink_to(target)


if __name__ == "__main__":
    main()
