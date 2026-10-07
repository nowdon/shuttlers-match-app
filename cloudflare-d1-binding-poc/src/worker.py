"""workers.dev-only entrypoint for the Flask + run_sync + D1 PoC."""
from urllib.parse import urlsplit

from workers import Response, WorkerEntrypoint, wsgi

from poc_app import allowed_path, safe_wsgi


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        method = str(request.method)
        path = urlsplit(request.url).path
        if not allowed_path(path):
            # Duplicate the outer boundary at the Worker layer; safe_wsgi remains
            # authoritative when invoked directly by a WSGI test.
            return Response("Not found", status=404)
        return await wsgi.fetch(safe_wsgi, request, self.env)
