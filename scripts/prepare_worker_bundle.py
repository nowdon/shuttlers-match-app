"""Stage only Worker source and public static files for pywrangler.

The existing requirements.txt is for the EC2 environment and is deliberately
not included: pywrangler rejects it when pyproject.toml is present.
"""

import argparse
from pathlib import Path
import shutil

if __package__:
    from .card_asset_inventory import audit_cards
else:
    from card_asset_inventory import audit_cards


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRECTORIES = ("data", "routes", "utils", "storage", "mail")
SOURCE_FILES = (
    "app.py", "worker.py", "logic.py", "models.py",
    "pyproject.toml", "uv.lock", "pylock.toml",
)


def prepare(destination, config):
    destination = Path(destination).resolve()
    config = Path(config).resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Worker staging directory must be empty")
    if not config.is_file():
        raise ValueError("Private Wrangler config is missing")
    cards_report = audit_cards(ROOT / "static/cards")
    if not cards_report["ok"]:
        raise ValueError(
            "Worker card assets are incomplete or invalid: "
            f"missing={cards_report['missing']}, unexpected={cards_report['unexpected']}, "
            f"invalid={cards_report['invalid']}, duplicates={cards_report['duplicate_content']}"
        )
    destination.mkdir(parents=True, exist_ok=True)
    for name in SOURCE_FILES:
        shutil.copy2(ROOT / name, destination / name)
    shutil.copy2(config, destination / "wrangler.jsonc")
    for directory in SOURCE_DIRECTORIES:
        target = destination / directory
        target.mkdir()
        for path in (ROOT / directory).glob("*.py"):
            shutil.copy2(path, target / path.name)
    templates = destination / "templates"
    templates.mkdir()
    for path in (ROOT / "templates").glob("*.html"):
        shutil.copy2(path, templates / path.name)
    static = destination / "static"
    static.mkdir()
    for path in (ROOT / "static").glob("*.csv"):
        shutil.copy2(path, static / path.name)
    cards = static / "cards"
    cards.mkdir()
    for item in cards_report["files"]:
        path = ROOT / item["path"]
        shutil.copy2(path, cards / path.name)
    return destination


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    print(prepare(args.destination, args.config))


if __name__ == "__main__":
    main()
