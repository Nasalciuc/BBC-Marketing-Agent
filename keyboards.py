"""BBC Marketing Agent — Inline Keyboards + callback parser.

Format callback_data: action_campaignId (compatibil cu split("_", 1))
Exemplu: approve_2026-W23-001, reject_URG-20260601-1430
"""


def review_keyboard(campaign_id: str) -> dict:
    """Review deal: Approve, Reject, Edit caption, Regenerate."""
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Approve", "callback_data": f"approve_{campaign_id}"},
                {"text": "❌ Reject", "callback_data": f"reject_{campaign_id}"},
            ],
            [
                {"text": "✏️ Edit caption", "callback_data": f"edit_{campaign_id}"},
                {"text": "🔄 Regenerate", "callback_data": f"regen_{campaign_id}"},
            ],
        ]
    }


def approved_keyboard(campaign_id: str) -> dict:
    """Post-approve status."""
    return {
        "inline_keyboard": [[
            {"text": "✅ Approved — broadcasting Monday", "callback_data": "noop"},
        ]]
    }


def rejected_keyboard() -> dict:
    """Post-reject status."""
    return {
        "inline_keyboard": [[
            {"text": "❌ Rejected", "callback_data": "noop"},
        ]]
    }


def urgent_keyboard(campaign_id: str) -> dict:
    """Urgent deal preview: Post NOW, Monday, Edit, Cancel."""
    return {
        "inline_keyboard": [
            [
                {"text": "📤 Post NOW", "callback_data": f"postnow_{campaign_id}"},
                {"text": "⏰ Monday", "callback_data": f"schedule_{campaign_id}"},
            ],
            [
                {"text": "✏️ Edit", "callback_data": f"edit_{campaign_id}"},
                {"text": "❌ Cancel", "callback_data": f"cancel_{campaign_id}"},
            ],
        ]
    }


def posted_keyboard() -> dict:
    """Post-broadcast status."""
    return {
        "inline_keyboard": [[
            {"text": "✅ Posted!", "callback_data": "noop"},
        ]]
    }


def parse_callback(data: str) -> tuple[str, str]:
    """Parse callback_data → (action, campaign_id). Compatibil cu format existent."""
    if not data or "_" not in data:
        return (data or ""), ""
    return data.split("_", 1)


def main_menu_keyboard() -> dict:
    """Main menu — 4 acțiuni rapide."""
    return {
        "inline_keyboard": [
            [
                {"text": "📊 Status", "callback_data": "cmd_status"},
                {"text": "📋 Deals", "callback_data": "cmd_deals"},
            ],
            [
                {"text": "⚡ New Deal", "callback_data": "cmd_urgent"},
            ],
            [
                {"text": "📖 Help", "callback_data": "cmd_help"},
            ],
        ]
    }


def nav_keyboard(back_to: str = "cmd_start") -> dict:
    """Navigation: Refresh + Back."""
    return {
        "inline_keyboard": [
            [
                {"text": "🔄 Refresh", "callback_data": back_to},
                {"text": "← Menu", "callback_data": "cmd_start"},
            ],
        ]
    }


def after_action_keyboard() -> dict:
    """After approve/post/schedule — next actions."""
    return {
        "inline_keyboard": [
            [
                {"text": "📊 Status", "callback_data": "cmd_status"},
                {"text": "⚡ New Deal", "callback_data": "cmd_urgent"},
                {"text": "← Menu", "callback_data": "cmd_start"},
            ],
        ]
    }
