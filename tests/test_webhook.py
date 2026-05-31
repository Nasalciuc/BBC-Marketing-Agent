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


def test_webhook_without_secret():
    resp = client.post("/webhook/telegram", json={})
    assert resp.status_code == 200
