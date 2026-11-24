import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def make_settings(tmp_path, **overrides) -> Settings:
    base = dict(
        api_keys=["sk-test-1", "sk-test-2"],
        database_url=f"sqlite:///{tmp_path}/app.db",
        allowed_origins=["http://localhost:3000"],
        enable_failure_simulation=True,
        cache_type="local",  # fresh in-memory LiteLLM cache per app instance
        cache_dir=str(tmp_path / "cache"),
        mock_llm=True,
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


def chat(client, session_id, message, headers=None, **extra):
    return client.post(
        "/chat", json={"session_id": session_id, "message": message, **extra}, headers=headers or {}
    )
