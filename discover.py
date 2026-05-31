"""BBC Marketing Agent — Weekly Deal Discovery.
Railway Cron: 0 14 * * 5 (vineri 14:00 UTC)
"""
import asyncio
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("bbc.discovery")


async def run_discovery():
    """Pipeline: discover → price → brand → upload → notify."""
    log.info("Starting weekly discovery...")

    try:
        from services.gemini_client import discover_and_verify
        from services.pricing_engine import calculate_price, format_price

        events = await discover_and_verify(week_offset=1, max_events=3)

        if not events:
            log.warning("No events found. Skipping.")
            return

        log.info("Found %d events", len(events))

        for event in events:
            route = event.get("routes", [{}])[0]
            from_iata = route.get("from", "JFK")
            to_iata = route.get("to", "LHR")
            price = calculate_price(from_iata, to_iata, "round_trip", "business")
            event["price"] = format_price(price) if price else "Contact us"
            event["price_raw"] = price
            log.info(
                "  %s: %s→%s = %s",
                event.get("name"),
                from_iata,
                to_iata,
                event["price"],
            )

        log.info("Discovery complete. %d events processed.", len(events))

    except Exception as e:
        log.error("Discovery FAILED: %s", e, exc_info=True)
        raise


if __name__ == "__main__":
    asyncio.run(run_discovery())
