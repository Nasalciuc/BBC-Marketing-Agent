"""
BBC Autonomous Marketing Agent — orchestrator.
Rulează pipeline-ul Betty (45+, foto+text, Getting There) și trimite
fiecare postare cu butoane Approve/Reject.

Triggers:
  - Cron Railway (Friday 14:00): python scripts/autonomous_agent.py
  - Telegram: director scrie "generează postări" → GENERATE_CONTENT intent
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("bbc.autonomous")


async def run_autonomous(count: int = 5) -> None:
    """Run the Betty pipeline end-to-end with approval buttons."""
    log.info("🤖 Autonomous run starting (count=%d)", count)
    from services.telegram_client import send_message

    try:
        import anthropic
        from google import genai

        from config import settings
        from scripts.real_footage_pipeline import step4_brand, step5_telegram
        from scripts.run_betty_lux import step1_search_45plus, step2_select_45plus

        await send_message(
            text=(
                "🤖 *Autonomous Agent Running*\n"
                f"_{datetime.now(UTC).strftime('%B %d, %Y · %H:%M UTC')}_\n\n"
                "🔍 Searching this week's best content..."
            )
        )

        gemini = genai.Client(api_key=settings.gemini_api_key)
        claude_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

        findings = step1_search_45plus(gemini)
        if not findings:
            await send_message(text="⚠️ No content found this week.")
            return

        selected = step2_select_45plus(claude_client, findings)[:count]
        if not selected:
            await send_message(text="⚠️ Nothing worth posting this week.")
            return

        await step4_brand(selected)
        await step5_telegram(selected)

        log.info("✅ Autonomous run complete: %d posts for approval", len(selected))

    except Exception as e:
        log.error("Autonomous run FAILED: %s", e, exc_info=True)
        try:
            from services.notifier import send_error_alert

            await send_error_alert(f"Autonomous agent failed:\n{e}")
        except Exception:
            pass
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="BBC Autonomous Marketing Agent")
    parser.add_argument("--count", type=int, default=5)
    args = parser.parse_args()
    asyncio.run(run_autonomous(count=args.count))


if __name__ == "__main__":
    main()
