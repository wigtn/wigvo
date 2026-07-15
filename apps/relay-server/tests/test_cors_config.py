"""CORS configuration tests."""

from src.config import Settings


def test_allowed_origins_from_string():
    """allowed_origins should be parsed from a comma-separated string."""
    s = Settings(allowed_origins="http://localhost:3000,https://wigvo.run")
    assert s.allowed_origins == ["http://localhost:3000", "https://wigvo.run"]


def test_allowed_origins_from_string_with_spaces():
    """Spaces around origins should be stripped."""
    s = Settings(allowed_origins="http://a.com , https://b.com , http://c.com")
    assert s.allowed_origins == ["http://a.com", "https://b.com", "http://c.com"]


def test_allowed_origins_from_list():
    """allowed_origins should accept a list directly."""
    origins = ["http://localhost:3000", "https://wigvo.run"]
    s = Settings(allowed_origins=origins)
    assert s.allowed_origins == origins


def test_allowed_origins_default():
    """Default allowed_origins should include localhost and production."""
    s = Settings()
    assert "http://localhost:3000" in s.allowed_origins
    assert "https://wigvo.run" in s.allowed_origins


def test_tenant_api_key_hashes_parse_from_json_env(monkeypatch):
    tenant_id = "10000000-0000-0000-0000-000000000001"
    digest = "a" * 64
    monkeypatch.setenv("TENANT_API_KEY_HASHES", f'{{"{tenant_id}":["{digest}"]}}')
    s = Settings(_env_file=None)
    assert s.tenant_api_key_hashes == {tenant_id: [digest]}
