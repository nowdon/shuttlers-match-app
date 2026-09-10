"""Produce the static template inventory used to set the render allowlist."""
import json
from pathlib import Path

from jinja2 import Environment, meta

from catalog import TEMPLATES


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent


def analyze():
    environment = Environment()
    rows = []
    for slug, spec in TEMPLATES.items():
        path = REPO / "templates" / spec["name"]
        source = path.read_text()
        parsed = environment.parse(source)
        rows.append(
            {
                "slug": slug,
                "template": spec["name"],
                "bytes": len(source.encode()),
                "referenced_templates": list(meta.find_referenced_templates(parsed)),
                "undeclared_variables": sorted(meta.find_undeclared_variables(parsed)),
                "load": True,
                "compile": True,
                "render": spec["render"],
                "classification": spec["classification"],
                "render_exclusion_reason": None if spec["render"] else spec["reason"],
            }
        )
    assert len(rows) == 14
    assert {row["template"] for row in rows} == {
        path.name for path in (REPO / "templates").glob("*.html")
    }
    return rows


if __name__ == "__main__":
    rows = analyze()
    (ROOT / "results").mkdir(exist_ok=True)
    destination = ROOT / "results" / "template-analysis.json"
    destination.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n")
    print(destination)
