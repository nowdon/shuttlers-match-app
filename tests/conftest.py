import pytest


@pytest.fixture(autouse=True)
def allow_dev_secret_key_for_tests(monkeypatch):
    monkeypatch.setenv("ALLOW_DEV_SECRET_KEY", "1")
