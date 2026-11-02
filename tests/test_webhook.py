"""Teste webhook — FastAPI endpoints."""
from fastapi.testclient import TestClient

from webhook import app

client = TestClient(app)


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


def test_root():
    resp = client.get("/")
    assert resp.status_code == 200


def test_webhook_refuses_when_secret_not_configured(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "telegram_webhook_secret", "")
    resp = client.post("/webhook/telegram", json={})
    assert resp.status_code == 503


def test_webhook_refuses_wrong_secret(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "telegram_webhook_secret", "right-secret")
    resp = client.post(
        "/webhook/telegram", json={}, headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"}
    )
    assert resp.status_code == 401


def test_webhook_accepts_right_secret(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "telegram_webhook_secret", "right-secret")
    resp = client.post(
        "/webhook/telegram",
        json={},
        headers={"X-Telegram-Bot-Api-Secret-Token": "right-secret"},
    )
    assert resp.status_code == 200


def test_debug_endpoint_and_api_docs_are_gone():
    assert client.get("/debug/gemini").status_code == 404
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
