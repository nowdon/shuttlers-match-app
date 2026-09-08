"""Standalone Flask application with no database, state or secrets."""

from flask import Blueprint, Flask, render_template

app = Flask(__name__, static_folder=None)


@app.get("/cloudflare-poc/health")
def health():
    return {
        "status": "ok",
        "runtime": "cloudflare-python-worker",
        "framework": "flask",
    }


poc_bp = Blueprint("cloudflare_poc", __name__, url_prefix="/cloudflare-poc")


@poc_bp.get("/blueprint")
def blueprint():
    return {"status": "ok", "blueprint": "cloudflare_poc"}


@poc_bp.get("/template")
def template():
    return render_template(
        "poc.html",
        project_name="shuttlers-match-app",
        title="Cloudflare Flask PoC",
    )


app.register_blueprint(poc_bp)
