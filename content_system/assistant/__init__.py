"""
Betty's assistant — the Telegram chat around the posts (PR-2), on top of content_system (PR-1).

    message from Betty ─┬─ a button (cs:… / ca:…)          → no AI: bridge / proposals
                        ├─ a menu button (📋 ✅ 💡 ❓)       → no AI: menu
                        ├─ a reply to "✏️ … · vN"           → the exact new caption (PR-1)
                        ├─ a reply to "💡 Idee nouă"         → saved as an idea, no AI
                        └─ any other text, or a reply to a post → the assistant (agent with tools)

The AI never approves, rejects or publishes. It reads the posts, rewrites a caption into a new
version that waits for Betty's tap, proposes "✅ Aprob …" cards whose buttons she taps, saves ideas
and passes on to Scaler what it can't do. Off until CONTENT_ASSISTANT_ENABLED=true.
"""
