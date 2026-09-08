"""Shared CPython/Pyodide probe. No application requests or runtime setup."""
import importlib
import json
import os
import sys

STAGES = (
    "flask", "sqlalchemy", "flask_sqlalchemy", "sqlite3", "models",
    "zoneinfo", "urllib.request", "ssl", "smtplib", "routes.helpers", "app",
)


def probe(name):
    if name == "app":
        from app import app
        return app
    if name == "models":
        from models import db
        return db
    if name == "zoneinfo":
        from zoneinfo import ZoneInfo
        return ZoneInfo("Asia/Tokyo")
    return importlib.import_module(name)


def attempt(name, detailed=False):
    try:
        probe(name)
        return {"stage": name, "status": "ok"}
    except Exception as error:
        if detailed:
            import traceback
            traceback.print_exc()  # CLI-only opt-in; never enabled by Worker.
        # Never serialize exception text, paths, or traceback to public HTTP.
        message = "Import or timezone construction failed."
        if type(error).__name__ == "ZoneInfoNotFoundError":
            message = "Required timezone data is unavailable."
        return {"stage": name, "status": "error",
                "exception_type": type(error).__name__,
                "message": message}


def run_diagnostics(detailed=False):
    # Audit/profile hooks observe and block forbidden effects without replacing
    # application functions. The audit hook remains installed but becomes inert.
    active = True
    counts = {"database_connect": 0, "runtime_file_open": 0,
              "network_connect": 0, "initialize_runtime": 0}

    def reject(key):
        counts[key] += 1
        raise RuntimeError("Forbidden import-time side effect")

    def audit(event, args):
        if not active:
            return
        if event == "sqlite3.connect":
            reject("database_connect")
        if event == "socket.connect":
            reject("network_connect")
        if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            path = os.fsdecode(args[0])
            if path.endswith(("config.json", "match_state.json", "draft_state.json", ".db")):
                reject("runtime_file_open")

    def profile(frame, event, arg):
        if event == "call" and frame.f_code.co_name == "initialize_runtime":
            reject("initialize_runtime")

    previous_profile = sys.getprofile()
    sys.addaudithook(audit)
    sys.setprofile(profile)
    try:
        # First try the real app import before individual probes warm imports.
        first = attempt("app", detailed)
        results = [attempt(name, detailed) for name in STAGES]
        imported = first["status"] == "ok"
        app_state = None
        if imported:
            module = sys.modules["app"]
            app_state = {
                "blueprints": sorted(module.app.blueprints),
                "secret_key_is_none": module.app.secret_key is None,
                "runtime_initialized": module._runtime_initialized,
            }
        pyodide = sys.modules.get("pyodide")
        return {
            "status": "ok" if imported and all(r["status"] == "ok" for r in results) else "error",
            "app_imported": imported, "initial_app_import": first,
            "results": results, "app": app_state, "blocked_attempts": counts,
            "python_version": sys.version.split()[0],
            "pyodide_version": getattr(pyodide, "__version__", None),
        }
    finally:
        active = False
        sys.setprofile(previous_profile)


if __name__ == "__main__":
    print(json.dumps(run_diagnostics(detailed="--tracebacks" in sys.argv), indent=2))
