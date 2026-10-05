import pytest

from vamos.brain import provider


@pytest.fixture(autouse=True)
def no_api_key(tmp_path_factory, monkeypatch):
    """No test may reach the real API: without a key the provider fails with "no API key"."""
    monkeypatch.setattr(provider, "REPO_ROOT", tmp_path_factory.mktemp("no-env"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
