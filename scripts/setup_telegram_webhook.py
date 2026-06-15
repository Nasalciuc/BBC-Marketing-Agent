"""Set Telegram webhook + live test. Reads TELEGRAM_* from env or .env."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
WEBHOOK_URL = "https://web-production-07bca.up.railway.app/webhook/telegram"
RAILWAY_HEALTH = "https://web-production-07bca.up.railway.app/health"


def load_telegram_config() -> tuple[str, str, str]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "914794275")
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("TELEGRAM_BOT_TOKEN=") and not token:
                token = line.split("=", 1)[1].strip().strip('"').strip("'")
            elif line.startswith("TELEGRAM_WEBHOOK_SECRET=") and not secret:
                secret = line.split("=", 1)[1].strip().strip('"').strip("'")
            elif line.startswith("TELEGRAM_CHAT_ID="):
                chat_id = line.split("=", 1)[1].strip().strip('"').strip("'")
    return token, secret, chat_id


def main() -> int:
    print("=== PAS 1: Railway Health ===")
    try:
        r = httpx.get(RAILWAY_HEALTH, timeout=10)
        print(f"Status: {r.status_code}")
        print(f"Response: {r.text}")
        data = r.json()
        assert data.get("status") == "ok"
        assert data.get("telegram") is True
        print("OK Railway LIVE + Telegram configured")
    except Exception as e:
        print(f"FAIL Railway: {e}")
        return 1

    print("\n=== PAS 2: Bot Token ===")
    token, secret, chat_id = load_telegram_config()
    if not token:
        print("FAIL TELEGRAM_BOT_TOKEN not set locally")
        print("Set: $env:TELEGRAM_BOT_TOKEN='...' (same as Railway)")
        return 1

    r = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
    data = r.json()
    if not data.get("ok"):
        print(f"FAIL Bot token invalid: {data}")
        return 1
    bot = data["result"]
    print(f"OK Bot: @{bot['username']} ({bot['first_name']})")

    print("\n=== PAS 3: Current Webhook ===")
    r = httpx.get(f"https://api.telegram.org/bot{token}/getWebhookInfo", timeout=10)
    wh = r.json().get("result", {})
    current_url = wh.get("url") or "[NOT SET]"
    print(f"URL: {current_url}")
    print(f"Pending: {wh.get('pending_update_count', 0)}")
    print(f"Last error: {wh.get('last_error_message', 'none')}")

    need_set = current_url != WEBHOOK_URL
    if not need_set:
        print("OK Webhook already set correctly")
    else:
        print(f"WARN Webhook needs update -> {WEBHOOK_URL}")

    print("\n=== PAS 4: Set Webhook ===")
    if need_set:
        payload: dict = {
            "url": WEBHOOK_URL,
            "allowed_updates": ["callback_query", "message"],
        }
        if secret:
            payload["secret_token"] = secret
        r = httpx.post(
            f"https://api.telegram.org/bot{token}/setWebhook",
            json=payload,
            timeout=10,
        )
        result = r.json()
        print(f"Result ok: {result.get('ok')}")
        if not result.get("ok"):
            print(f"FAIL setWebhook: {result.get('description', result)}")
            return 1
        print(f"OK Webhook SET: {WEBHOOK_URL}")

    r = httpx.get(f"https://api.telegram.org/bot{token}/getWebhookInfo", timeout=10)
    wh = r.json().get("result", {})
    print(f"Confirmed URL: {wh.get('url')}")
    print(f"Pending updates: {wh.get('pending_update_count', 0)}")

    print("\n=== PAS 5: Test Message ===")
    r = httpx.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": (
                "🧪 BBC Marketing Agent — webhook live test.\n\n"
                "Dacă vezi asta, sistemul funcționează!\n\n"
                "Trimite /start pentru meniu."
            ),
            "parse_mode": "Markdown",
        },
        timeout=10,
    )
    result = r.json()
    if result.get("ok"):
        print(f"OK Test message SENT to {chat_id}")
        print(f"   Message ID: {result['result']['message_id']}")
    else:
        print(f"FAIL Send: {result.get('description', result)}")
        return 1

    print("\n=== PAS 6: Final Check ===")
    checks: dict[str, str] = {}
    try:
        r = httpx.get(RAILWAY_HEALTH, timeout=10)
        checks["Railway /health"] = "OK" if r.status_code == 200 else "FAIL"
    except Exception:
        checks["Railway /health"] = "FAIL timeout"

    try:
        r = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
        checks["Bot getMe"] = (
            "OK @" + r.json()["result"]["username"] if r.json().get("ok") else "FAIL"
        )
    except Exception:
        checks["Bot getMe"] = "FAIL"

    try:
        r = httpx.get(f"https://api.telegram.org/bot{token}/getWebhookInfo", timeout=10)
        wh = r.json().get("result", {})
        url = wh.get("url", "")
        checks["Webhook URL"] = "OK" if url == WEBHOOK_URL else f"FAIL {url}"
        err = wh.get("last_error_message", "")
        checks["Webhook errors"] = "OK none" if not err else f"WARN {err}"
    except Exception:
        checks["Webhook"] = "FAIL"

    try:
        r = httpx.get(RAILWAY_HEALTH, timeout=10)
        checks["Telegram on Railway"] = "OK" if r.json().get("telegram") else "FAIL"
    except Exception:
        checks["Telegram on Railway"] = "FAIL"

    for k, v in checks.items():
        print(f"  {v:30s} {k}")

    all_ok = all(str(v).startswith("OK") for v in checks.values())
    print()
    print("ALL GOOD — bot is LIVE" if all_ok else "ISSUES FOUND")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
