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
