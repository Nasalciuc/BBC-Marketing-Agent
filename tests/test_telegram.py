"""Teste Telegram client — verifică format mesaje."""


def test_telegram_imports():
    from services.telegram_client import (
        send_approval_request,
        send_message,
        send_video,
    )

    assert callable(send_message)
    assert callable(send_approval_request)
    assert callable(send_video)


def test_callback_data_format():
    campaign_id = "2026-W23-001"
    approve_data = f"approve_{campaign_id}"
    reject_data = f"reject_{campaign_id}"

    action, cid = approve_data.split("_", 1)
    assert action == "approve"
    assert cid == campaign_id

    action2, cid2 = reject_data.split("_", 1)
    assert action2 == "reject"
    assert cid2 == campaign_id
