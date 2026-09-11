"""Build an allowlisted Worker tree; never copy repository runtime data."""
import hashlib
import json
import os
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
TARGET = ROOT / ".build"


def build(target=TARGET, manifest_path=None):
    target = Path(target)
    if target.exists():
        raise FileExistsError(f"Refusing to overwrite build/evidence directory: {target}")
    src = target / "src"
    src.mkdir(parents=True)

    def link(source, relative):
        destination = src / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(os.path.relpath(source, destination.parent))

    for name in ("worker.py", "side_effect_guard.py"):
        link(ROOT / "src" / name, name)
    for name in ("app.py", "logic.py", "models.py"):
        link(REPO / name, name)
    for directory in ("routes", "data", "utils"):
        for source in sorted((REPO / directory).glob("*.py")):
            link(source, str(source.relative_to(REPO)))

    for name in ("pyproject.toml", "pylock.toml", "uv.lock", "wrangler.jsonc"):
        shutil.copyfile(ROOT / name, target / name)

    files = {
        str(path.relative_to(src)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(src.rglob("*"))
        if path.is_file()
    }
    manifest = {
        "worker": "shuttlers-match-runtime-poc",
        "source_files": files,
        "template_files": 0,
        "static_files": 0,
        "runtime_data_files": 0,
    }
    destination = Path(manifest_path or ROOT / "results" / "manifest.json")
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(manifest, indent=2) + "\n")
    return target


if __name__ == "__main__":
    print(build())
