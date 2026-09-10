"""Build an allowlisted Python Worker root without runtime data or static files."""
import hashlib
import json
import os
from pathlib import Path
import shutil

from catalog import TEMPLATES


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
TARGET = ROOT / ".build"


def build(target=TARGET, manifest_path=None):
    target = Path(target)
    if target.exists():
        raise FileExistsError(f"Refusing to overwrite evidence/build directory: {target}")
    src = target / "src"
    src.mkdir(parents=True)

    def link(source, relative):
        destination = src / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(os.path.relpath(source, destination.parent))

    for name in ("worker.py", "side_effect_guard.py"):
        link(ROOT / "src" / name, name)
    link(ROOT / "catalog.py", "catalog.py")
    for name in ("app.py", "logic.py", "models.py"):
        link(REPO / name, name)
    for directory in ("routes", "data", "utils"):
        for source in sorted((REPO / directory).glob("*.py")):
            link(source, str(source.relative_to(REPO)))
    for spec in TEMPLATES.values():
        source = REPO / "templates" / spec["name"]
        link(source, f"templates/{source.name}")

    for name in ("pyproject.toml", "pylock.toml", "uv.lock", "wrangler.jsonc"):
        shutil.copyfile(ROOT / name, target / name)

    source_files = {
        str(path.relative_to(src)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(src.rglob("*"))
        if path.is_file()
    }
    manifest = {
        "worker": "shuttlers-match-jinja-poc",
        "source_files": source_files,
        "template_count": len(TEMPLATES),
        "static_files": 0,
    }
    manifest_path = Path(manifest_path or ROOT / "results" / "manifest.json")
    manifest_path.parent.mkdir(exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2) + "\n"
    )
    return target


if __name__ == "__main__":
    print(build())
