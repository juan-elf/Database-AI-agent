"""
pytest configuration — ensure tests always run against SQLite,
regardless of DATABASE_URL in the local .env.
"""
import os
import pytest


@pytest.fixture(autouse=True)
def _isolate_from_postgres(monkeypatch):
    """Unset DATABASE_URL so all tests use the SQLite engine."""
    monkeypatch.delenv("DATABASE_URL", raising=False)


@pytest.fixture(autouse=True)
def _bypass_llm_guardrail(monkeypatch, request):
    """
    Bypass check_input_with_llm in all tests except test_guardrails.py.
    The LLM classifier makes a real API call; unit tests shouldn't depend on it.
    test_guardrails.py tests the guardrail logic directly with a mocked client.
    """
    if "test_guardrails" in str(request.fspath):
        return
    import agent
    monkeypatch.setattr(agent, "check_input_with_llm", lambda text, client, model, **kw: (True, ""))
