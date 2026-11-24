from app.config import Settings, sqlite_path_from_url
from app.db import Database
from app.llm import parse_simulation


def test_parse_simulation():
    assert parse_simulation("key_1=429") == {"key_1": 429}
    assert parse_simulation("key_1=429, key_2=500") == {"key_1": 429, "key_2": 500}
    assert parse_simulation("*=429") == {"*": 429}
    assert parse_simulation("key_1=404,garbage,=500,key_2=abc") == {}
    assert parse_simulation(None) == {}


def test_sqlite_url_parsing():
    assert sqlite_path_from_url("sqlite:////data/app.db") == "/data/app.db"
    assert sqlite_path_from_url("sqlite:///./app.db") == "./app.db"


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("LLM_API_KEYS", "a, b ,c")
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://x.com,https://y.com")
    monkeypatch.setenv("ENABLE_FAILURE_SIMULATION", "true")
    monkeypatch.setenv("DATABASE_URL", "sqlite:////data/app.db")
    s = Settings.from_env()
    assert s.api_keys == ["a", "b", "c"]
    assert s.allowed_origins == ["http://x.com", "https://y.com"]
    assert s.enable_failure_simulation is True
    assert s.cache_dir == "/data/cache"


def test_schema_created_on_init(tmp_path):
    db = Database(str(tmp_path / "sub" / "app.db"))
    db.init()
    with db.connect() as conn:
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(messages)")]
    assert cols == ["id", "session_id", "role", "content", "metadata", "created_at"]
