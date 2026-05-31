"""BBC Marketing Agent — Weekly Deal Broadcast.
Railway Cron: 0 10 * * 1 (luni 10:00 UTC)
"""
import asyncio
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("bbc.broadcast")


async def run_broadcast():
    """Pipeline: read approved → broadcast Twilio → post WAHA → report."""
    log.info("Starting weekly broadcast...")

    try:
        log.info("Broadcast not yet implemented. Skipping.")

    except Exception as e:
        log.error("Broadcast FAILED: %s", e, exc_info=True)
        raise


if __name__ == "__main__":
    asyncio.run(run_broadcast())
