"""Protect synthetic developer data while exercising real integration tests."""

import hashlib
import importlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
import pytest

from conftest import clear_app_modules


ROOT = Path(__file__).resolve().parents[1]


def test_real_engine_and_reimports_use_temporary_instance(isolated_runtime):
    first = importlib.import_module('app')
    first.initialize_runtime()
    clear_app_modules()
    second = importlib.import_module('app')
    assert first.app is not second.app
    for module in (first, second):
        with module.app.app_context():
            expected = isolated_runtime / 'instance' / 'participants.db'
            assert Path(module.app.instance_path) == expected.parent
            assert Path(module.db.engine.url.database) == expected
            assert module.app.config['SQLALCHEMY_DATABASE_URI'] == f'sqlite:///{expected}'


def test_database_outside_test_root_is_rejected_before_init(tmp_path):
    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{tmp_path.parent / "unsafe.db"}'
    database = SQLAlchemy()
    with pytest.raises(AssertionError, match='outside temporary root'):
        database.init_app(app)
    assert 'sqlalchemy' not in app.extensions


def test_integration_tests_preserve_synthetic_repository_data(tmp_path):
    repository = tmp_path / 'repository'
    repository.mkdir()
    for name in ('app.py', 'models.py', 'logic.py', 'init_db.py', 'gunicorn.conf.py', 'pytest.ini', '.gitignore'):
        shutil.copy2(ROOT / name, repository / name)
    for name in ('utils', 'routes', 'data', 'templates', 'tests'):
        shutil.copytree(ROOT / name, repository / name,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    protected = (
        'instance/participants.db', 'instance/history_dumps/existing.json',
        'config.json', 'match_state.json', 'draft_state.json',
    )
    for name in protected:
        path = repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        # Deliberately invalid DB/JSON: any accidental runtime read also fails.
        path.write_bytes(b'developer-data-must-remain-unchanged')

    def snapshot():
        paths = list((repository / 'instance').rglob('*'))
        paths += [repository / name for name in protected[2:]]
        return {str(path.relative_to(repository)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in paths if path.is_file()}

    before = snapshot()
    nodes = [
        'test_match_history.py', 'test_line_notification_routes.py', 'test_stats.py',
        'test_reset.py', 'test_runtime_initialization.py', 'test_match_draft.py',
        'test_secret_key.py', 'test_flash_messages.py', 'test_paypay_expiration.py',
    ]
    env = dict(os.environ, PYTHONPATH=str(repository), PYTHONDONTWRITEBYTECODE='1')
    result = subprocess.run(
        [sys.executable, '-B', '-m', 'pytest', '-q',
         *[f'tests/{name}' for name in nodes]],
        cwd=repository, env=env, capture_output=True, text=True, timeout=90,
    )
    assert snapshot() == before
    assert result.returncode == 0, result.stdout + result.stderr
