"""Import isolation and production startup regression tests."""

from concurrent.futures import ThreadPoolExecutor
import importlib
import io
import os
from pathlib import Path
import runpy
import subprocess
import sys
from unittest.mock import Mock

import flask
import pytest
from sqlalchemy import inspect

from conftest import clear_app_modules


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('target', ['app', 'routes.helpers', 'init_db'])
def test_import_without_runtime_io_or_secret(tmp_path, target):
    script = r'''
import builtins
import os
import sys
from unittest.mock import patch
import models
import utils.config
import utils.match_state
import utils.draft_state
from sqlalchemy.engine import Engine

real_open = builtins.open

def guarded_open(path, mode='r', *args, **kwargs):
    if isinstance(path, (str, bytes, os.PathLike)):
        assert not os.fsdecode(path).endswith('.json'), path
    assert not any(flag in mode for flag in 'wax+'), (path, mode)
    return real_open(path, mode, *args, **kwargs)

def forbidden(*args, **kwargs):
    raise AssertionError('runtime I/O during import')

def profile(frame, event, arg):
    if event == 'call' and frame.f_code.co_name in {
        'ensure_database_tables', 'ensure_match_history_score_text_column',
        'initialize_runtime',
    }:
        forbidden()

sys.setprofile(profile)
with patch.object(builtins, 'open', guarded_open), \
     patch.object(os, 'makedirs', forbidden), \
     patch.object(Engine, 'connect', forbidden), \
     patch.object(models.db, 'create_all', forbidden), \
     patch.object(utils.config, 'load_config', forbidden), \
     patch.object(utils.config, 'load_raw_config', forbidden), \
     patch.object(utils.match_state, 'load_match_state', forbidden), \
     patch.object(utils.draft_state, 'get_active_draft', forbidden):
    __import__(sys.argv[1])
    if sys.argv[1] == 'app':
        from app import app
        assert {'api', 'participant', 'admin', 'match', 'history', 'line'} <= set(app.blueprints)
        assert app.secret_key is None
sys.setprofile(None)
print('import ok')
'''
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE='1')
    env.pop('SECRET_KEY', None)
    env.pop('ALLOW_DEV_SECRET_KEY', None)
    result = subprocess.run(
        [sys.executable, '-B', '-c', script, target], cwd=tmp_path,
        env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'import ok'
    assert list(tmp_path.iterdir()) == []


@pytest.fixture
def runtime_app(monkeypatch, tmp_path):
    # Isolate the real SQLite schema and config from the user's instance data.
    original_flask = flask.Flask
    monkeypatch.setattr(flask, 'Flask', lambda *args, **kwargs: original_flask(
        *args, **kwargs, instance_path=str(tmp_path / 'instance'),
    ))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('SECRET_KEY', 'runtime-test-secret')
    monkeypatch.delenv('ALLOW_DEV_SECRET_KEY', raising=False)
    clear_app_modules()
    module = importlib.import_module('app')
    yield module
    with module.app.app_context():
        module.db.session.remove()
        module.db.engine.dispose()
    clear_app_modules()


def test_gunicorn_hook_initializes_once_before_requests(runtime_app, monkeypatch):
    module = runtime_app
    create_all = Mock(wraps=module.db.create_all)
    compatibility = Mock(wraps=module.ensure_match_history_score_text_column)
    monkeypatch.setattr(module.db, 'create_all', create_all)
    monkeypatch.setattr(module, 'ensure_match_history_score_text_column', compatibility)
    hook = runpy.run_path(str(ROOT / 'gunicorn.conf.py'))['post_worker_init']
    hook(None)
    hook(None)
    client = module.app.test_client()
    assert client.get('/').status_code == 302
    assert client.get('/').status_code == 302
    create_all.assert_called_once_with()
    compatibility.assert_called_once_with()
    with module.app.app_context():
        assert 'score_text' in {
            column['name'] for column in inspect(module.db.engine).get_columns('match_histories')
        }


def test_wsgi_initializes_before_session_and_only_once(runtime_app, monkeypatch):
    module = runtime_app
    monkeypatch.delenv('SECRET_KEY')
    monkeypatch.setenv('ALLOW_DEV_SECRET_KEY', '1')
    module.app.secret_key = None
    initialize = Mock(wraps=module.ensure_database_tables)
    monkeypatch.setattr(module, 'ensure_database_tables', initialize)
    module.save_config({'level_map': {}, 'gender_weight': {}})
    client = module.app.test_client()
    for _ in range(2):
        assert client.post('/register', data={'card': '♥A'}).status_code == 302
        with client.session_transaction() as session:
            assert session['_flashes']
    initialize.assert_called_once_with()


def test_concurrent_runtime_initialization_runs_once(runtime_app, monkeypatch):
    initialize = Mock(wraps=runtime_app.ensure_database_tables)
    monkeypatch.setattr(runtime_app, 'ensure_database_tables', initialize)
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda _: runtime_app.initialize_runtime(), range(8)))
    initialize.assert_called_once_with()


def test_startup_rejects_missing_secret_before_database(runtime_app, monkeypatch):
    monkeypatch.delenv('SECRET_KEY')
    initialize = Mock()
    monkeypatch.setattr(runtime_app, 'ensure_database_tables', initialize)
    hook = runpy.run_path(str(ROOT / 'gunicorn.conf.py'))['post_worker_init']
    with pytest.raises(RuntimeError, match='SECRET_KEY is required'):
        hook(None)
    with pytest.raises(RuntimeError, match='SECRET_KEY is required'):
        runtime_app.app.test_client().get('/')
    initialize.assert_not_called()
    assert not runtime_app._runtime_initialized


def test_failed_schema_initialization_can_retry(runtime_app, monkeypatch):
    initialize = Mock(side_effect=[RuntimeError('schema failed'), None])
    monkeypatch.setattr(runtime_app, 'ensure_database_tables', initialize)
    with pytest.raises(RuntimeError, match='schema failed'):
        runtime_app.initialize_runtime()
    assert not runtime_app._runtime_initialized
    runtime_app.initialize_runtime()
    assert initialize.call_count == 2
    assert runtime_app._runtime_initialized


def test_cli_and_init_db_share_runtime_initialization(runtime_app, monkeypatch):
    initialize = Mock(wraps=runtime_app.ensure_database_tables)
    monkeypatch.setattr(runtime_app, 'ensure_database_tables', initialize)
    result = runtime_app.app.test_cli_runner().invoke(args=['init-runtime'])
    assert result.exit_code == 0, result.output
    runpy.run_path(str(ROOT / 'init_db.py'), run_name='__main__')
    initialize.assert_called_once_with()


def test_settings_update_refreshes_registration_and_csv_weights(runtime_app):
    module = runtime_app
    module.save_config({'level_map': {'beginner': 1}, 'gender_weight': {'male': 1.0}})
    client = module.app.test_client()
    assert client.post('/register', data={
        'name': 'before', 'gender': 'male', 'level': 'beginner', 'card': '♥A',
    }).status_code == 302
    assert client.post('/admin/settings', data={
        'level_beginner': '7', 'weight_male': '1.5',
    }).status_code == 302
    assert client.post('/register', data={
        'name': 'after', 'gender': 'male', 'level': 'beginner', 'card': '♥2',
    }).status_code == 302
    assert client.post('/upload', data={'file': (
        io.BytesIO('name,gender,level,card\ncsv,male,beginner,♥3\n'.encode()), 'players.csv',
    )}).status_code == 302
    with module.app.app_context():
        assert {p.name: p.weight for p in module.Participant.query.all()} == {
            'before': 1.0, 'after': 10.5, 'csv': 10.5,
        }
