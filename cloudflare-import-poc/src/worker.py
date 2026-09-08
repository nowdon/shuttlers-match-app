"""Import probe only: never dispatch a request to the production Flask app."""
from workers import Response, WorkerEntrypoint
from diagnostics import run_diagnostics

REPORT = run_diagnostics()


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        from urllib.parse import urlsplit

        path = urlsplit(request.url).path
        if path == "/cloudflare-import-poc/diagnostics":
            return Response.json(REPORT)
        if path == "/cloudflare-import-poc/health" and REPORT["app_imported"]:
            return Response.json({
                "status": "ok", "app_imported": True,
                "blueprints": REPORT["app"]["blueprints"],
            })
        return Response("Not found", status=404)
