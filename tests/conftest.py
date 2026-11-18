import pytest


@pytest.fixture(autouse=True)
def offline_backend(monkeypatch):
    """Keep the test suite deterministic: never call a real model API from tests."""
    monkeypatch.setenv("PAWPAL_LLM", "offline")
