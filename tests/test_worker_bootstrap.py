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
    workers.Response = lambda body, **kwargs: SimpleNamespace(body=body, **kwargs)
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
    request = SimpleNamespace(url='https://example.test/viewer', method='GET')

    assert asyncio.run(entry.fetch(request)) == 'response'
    assert module.app.config['SECRET_KEY'] == 'local-secret'
    assert module.app.config['STORAGE_BACKEND'] == 'd1'
    assert module.app.config['HISTORY_ARCHIVE_BACKEND'] == 'r2'
    assert module.app.config['PHASE14_PRE_CUTOVER_READ_ONLY'] == 'false'
    assert 'DB' not in module.app.config
    assert 'HISTORY_ARCHIVES' not in module.app.config
    assert 'STORAGE_BACKEND' not in __import__('os').environ
    workers.wsgi.fetch.assert_awaited_once_with(module.app, request, env)


def test_worker_bridges_line_and_mail_scalars_without_object_bindings(worker_module, monkeypatch):
    module, workers = worker_module
    from routes.helpers import has_required_line_messaging_config, is_line_messaging_enabled
    from utils.line_push import verify_line_signature
    from mail.provider import selected_mail_transport
    from utils.mail_sender import _sender_settings
    import base64
    import hashlib
    import hmac

    monkeypatch.delenv('LINE_MESSAGING_ENABLED', raising=False)
    monkeypatch.delenv('LINE_CHANNEL_SECRET', raising=False)
    monkeypatch.delenv('LINE_CHANNEL_ACCESS_TOKEN', raising=False)
    monkeypatch.delenv('MAIL_TRANSPORT', raising=False)
    monkeypatch.delenv('MAIL_FROM_EMAIL', raising=False)
    env = SimpleNamespace(
        SECRET_KEY='local-secret', STORAGE_BACKEND='d1',
        HISTORY_ARCHIVE_BACKEND='r2', LINE_MESSAGING_ENABLED='true',
        LINE_CHANNEL_SECRET='synthetic-secret',
        LINE_CHANNEL_ACCESS_TOKEN='synthetic-access-token',
        MAIL_TRANSPORT='cloudflare', MAIL_FROM_EMAIL='test@example.com',
        DB=object(), HISTORY_ARCHIVES=object(), EMAIL=object(),
    )
    entry = module.Default()
    entry.env = env
    asyncio.run(entry.fetch(SimpleNamespace(url='https://example.test/viewer', method='GET')))

    with module.app.app_context():
        assert is_line_messaging_enabled()
        assert has_required_line_messaging_config()
        body = b'{"events":[]}'
        signature = base64.b64encode(hmac.new(
            b'synthetic-secret', body, hashlib.sha256,
        ).digest()).decode('ascii')
        assert verify_line_signature(body, signature, module.app.config['LINE_CHANNEL_SECRET'])
        assert selected_mail_transport() == 'cloudflare'
        assert _sender_settings()[0] == 'test@example.com'
    assert 'DB' not in module.app.config
    assert 'HISTORY_ARCHIVES' not in module.app.config
    assert 'EMAIL' not in module.app.config
    workers.wsgi.fetch.assert_awaited_once()


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
        asyncio.run(entry.fetch(SimpleNamespace(url='https://example.test/viewer', method='GET')))
    workers.wsgi.fetch.assert_not_awaited()


def test_worker_static_assets_use_request_local_binding(worker_module):
    module, workers = worker_module
    assets = SimpleNamespace(fetch=AsyncMock(return_value='asset-response'))
    entry = module.Default()
    entry.env = SimpleNamespace(
        SECRET_KEY='local-secret', STORAGE_BACKEND='d1',
        HISTORY_ARCHIVE_BACKEND='r2', ASSETS=assets,
    )
    request = SimpleNamespace(
        url='https://example.test/static/participants_template.csv', method='GET',
    )
    assert asyncio.run(entry.fetch(request)) == 'asset-response'
    assets.fetch.assert_awaited_once_with('https://assets.local/participants_template.csv')
    workers.wsgi.fetch.assert_not_awaited()
    assert 'ASSETS' not in module.app.config


def test_http_redirect_preserves_request_authority_path_and_query(worker_module):
    module, workers = worker_module
    entry = module.Default()
    entry.env = SimpleNamespace()
    for source, expected in (
        ('http://example.com/admin/settings?mode=admin',
         'https://example.com/admin/settings?mode=admin'),
        ('http://example.com:8080/a%2Fb?q=a%26b&mode=admin&mode=viewer',
         'https://example.com:8080/a%2Fb?q=a%26b&mode=admin&mode=viewer'),
        ('http://[2001:db8::1]:8080/viewer',
         'https://[2001:db8::1]:8080/viewer'),
        ('http://example.com/', 'https://example.com/'),
    ):
        request = SimpleNamespace(url=source, method='GET')
        response = asyncio.run(entry.fetch(request))
        assert response.status == 308
        assert response.headers['Location'] == expected
    workers.wsgi.fetch.assert_not_awaited()

    entry.env = SimpleNamespace(SECRET_KEY='synthetic-secret', STORAGE_BACKEND='d1')
    request = SimpleNamespace(
        url='https://example.com/admin/settings?mode=admin', method='GET',
    )
    assert asyncio.run(entry.fetch(request)) == 'response'
    workers.wsgi.fetch.assert_awaited_once_with(module.app, request, entry.env)


def test_pre_cutover_gate_rejects_unauthorized_and_mutating_requests(worker_module):
    module, workers = worker_module
    entry = module.Default()
    entry.env = SimpleNamespace(
        PHASE14_PRE_CUTOVER_READ_ONLY='true',
        PHASE14_SMOKE_TOKEN='synthetic-token', PHASE14_VERSION='v-test',
        SECRET_KEY='local-secret', STORAGE_BACKEND='d1',
    )

    def request(method, token=None, path='/viewer'):
        return SimpleNamespace(url='https://example.test' + path, method=method,
                               headers={'X-Phase14-Smoke-Token': token} if token else {})

    denied = asyncio.run(entry.fetch(request('GET')))
    assert denied.status == 404
    blocked = asyncio.run(entry.fetch(request('POST', 'synthetic-token')))
    assert blocked.status == 405
    ready = asyncio.run(entry.fetch(request('GET', 'synthetic-token',
                                            '/__phase14/readiness')))
    assert ready.body == 'v-test'
    assert ready.headers['X-Phase14-Version'] == 'v-test'
    workers.wsgi.fetch.assert_not_awaited()


def test_pre_cutover_empty_d1_config_is_read_only(worker_module, monkeypatch):
    module, workers = worker_module
    from utils import config
    storage = SimpleNamespace(first=lambda *_args: None)
    monkeypatch.setattr(config, 'selected_storage_backend', lambda: 'd1')
    monkeypatch.setattr(config, 'get_storage', lambda: storage)
    entry = module.Default()
    entry.env = SimpleNamespace(
        PHASE14_PRE_CUTOVER_READ_ONLY='true',
        PHASE14_SMOKE_TOKEN='synthetic-token', PHASE14_VERSION='v-test',
        SECRET_KEY='local-secret', STORAGE_BACKEND='d1',
    )
    request = SimpleNamespace(url='https://example.test/admin/settings', method='GET',
                              headers={'X-Phase14-Smoke-Token': 'synthetic-token'})
    asyncio.run(entry.fetch(request))
    with module.app.app_context():
        assert config.load_raw_config_with_version() == ({}, 0)
        module.app.config['PHASE14_PRE_CUTOVER_READ_ONLY'] = 'false'
        with pytest.raises(config.StorageUnavailableError):
            config.load_raw_config_with_version()
