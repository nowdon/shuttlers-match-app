"""Local-only entrypoint for the production R2 adapter WSGI probe."""

from workers import WorkerEntrypoint, wsgi

from poc_app import application


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return await wsgi.fetch(application, request, self.env)
