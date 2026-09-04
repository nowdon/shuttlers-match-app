import sys

import pytest


@pytest.fixture(autouse=True)
def allow_dev_secret_key_for_tests(monkeypatch):
    monkeypatch.setenv("ALLOW_DEV_SECRET_KEY", "1")


ROUTE_MODULE_NAMES = (
    "routes.helpers",
    "routes.participant",
    "routes.line",
    "routes.admin",
    "routes.match",
    "routes.history",
)


def clear_app_modules():
    sys.modules.pop("app", None)
    for module_name in ROUTE_MODULE_NAMES:
        sys.modules.pop(module_name, None)


def patch_app_dependency(monkeypatch, app_module, name, value):
    monkeypatch.setattr(app_module, name, value)
    for module_name in ROUTE_MODULE_NAMES:
        route_module = sys.modules.get(module_name)
        if route_module is not None and hasattr(route_module, name):
            monkeypatch.setattr(route_module, name, value)
