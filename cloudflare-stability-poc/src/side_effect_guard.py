"""PoC-only synchronous guards; no await may occur inside a guard scope."""
from contextlib import contextmanager
from contextvars import ContextVar
import os
import sys

KEYS = ('database_connect', 'runtime_file_open', 'config_state_open',
        'network_connect', 'initialize_runtime')
ACTIVE = ContextVar('wsgi_poc_guard', default=None)


def reject(key):
    counts = ACTIVE.get()
    if counts is not None:
        counts[key] += 1
        raise RuntimeError('Forbidden PoC side effect')


def audit(event, args):
    if ACTIVE.get() is None:
        return
    if event == 'sqlite3.connect':
        reject('database_connect')
    if event == 'socket.connect':
        reject('network_connect')
    if event == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
        path = os.fsdecode(args[0]).replace('\\', '/')
        name = path.rsplit('/', 1)[-1]
        if name in ('config.json', 'match_state.json', 'draft_state.json'):
            reject('config_state_open')
        if name.endswith(('.db', '.db-wal', '.db-shm', '.db-journal')) or '/history_dumps/' in path:
            reject('runtime_file_open')


def profile(frame, event, arg):
    if event == 'call' and frame.f_code.co_name == 'initialize_runtime':
        reject('initialize_runtime')


sys.addaudithook(audit)


@contextmanager
def guard():
    counts = dict.fromkeys(KEYS, 0)
    token = ACTIVE.set(counts)
    previous = sys.getprofile()
    sys.setprofile(profile)
    try:
        yield counts
    finally:
        sys.setprofile(previous)
        ACTIVE.reset(token)
