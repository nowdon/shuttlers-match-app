"""Default Gunicorn startup hooks; keep the existing app:app target."""


def post_worker_init(worker):
    # Also runs with --preload, after the application is loaded in the worker.
    from app import initialize_runtime

    initialize_runtime()
