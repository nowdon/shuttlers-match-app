from workers import Response, WorkerEntrypoint


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return Response('stage A ok', headers={'X-Stability-Stage': 'A'})
