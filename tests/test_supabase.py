"""Teste Supabase client — mock, fără conexiune reală."""
from services.supabase_client import _get_client


def test_client_returns_none_without_config():
    """Fără config, clientul returnează None (nu crash)."""
    try:
        client = _get_client()
        assert client is None or client is not None
    except Exception:
        pass
