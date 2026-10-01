"""Worker scalar configuration is explicit and fails closed."""

import asyncio
import importlib
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def worker_module(monkeypatch):
    workers = ModuleType('workers')
    workers.WorkerEntrypoint = object
    workers.wsgi = SimpleNamespace(fetch=AsyncMock(return_value='response'))
    monkeypatch.setitem(sys.modules, 'workers', workers)
    sys.modules.pop('worker', None)
    module = importlib.import_module('worker')
    yield module, workers
    sys.modules.pop('worker', None)


def test_worker_bootstrap_sets_only_scalar_flask_config(worker_module, monkeypatch):
    module, workers = worker_module
    monkeypatch.delenv('STORAGE_BACKEND', raising=False)
    monkeypatch.delenv('SECRET_KEY', raising=False)
    env = SimpleNamespace(SECRET_KEY='local-secret', STORAGE_BACKEND='d1',
                          HISTORY_ARCHIVE_BACKEND='r2', DB=object(),
                          HISTORY_ARCHIVES=object())
    entry = module.Default()
    entry.env = env
    request = object()

    assert asyncio.run(entry.fetch(request)) == 'response'
    assert module.app.config['SECRET_KEY'] == 'local-secret'
    assert module.app.config['STORAGE_BACKEND'] == 'd1'
    assert module.app.config['HISTORY_ARCHIVE_BACKEND'] == 'r2'
    assert 'DB' not in module.app.config
    assert 'HISTORY_ARCHIVES' not in module.app.config
    assert 'STORAGE_BACKEND' not in __import__('os').environ
    workers.wsgi.fetch.assert_awaited_once_with(module.app, request, env)


@pytest.mark.parametrize('env', [
    SimpleNamespace(STORAGE_BACKEND='d1'),
    SimpleNamespace(SECRET_KEY='local-secret'),
    SimpleNamespace(SECRET_KEY='local-secret', STORAGE_BACKEND='sqlite'),
])
def test_worker_bootstrap_rejects_missing_secret_or_d1(worker_module, env, monkeypatch):
    module, workers = worker_module
    monkeypatch.setenv('SECRET_KEY', 'host-secret')
    entry = module.Default()
    entry.env = env
    with pytest.raises(RuntimeError):
        asyncio.run(entry.fetch(object()))
    workers.wsgi.fetch.assert_not_awaited()
