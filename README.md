# BBC Marketing Agent

Automated marketing pipeline for [BuyBusinessClass](https://buybusinessclass.com): deal discovery, pricing, branded creatives, and multi-channel broadcast.

## Phase 1 (current)

- Project structure
- `config.py` (pydantic-settings)
- `services/pricing_engine.py` — BBC pricing formula
- `data/price_generator_rules.json` — derived from live BBC API
- `data/airports.json` — 61 major airports
- `tests/test_pricing.py` — 15 pricing tests

## Setup

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
pytest tests/test_pricing.py -v
```

## Architecture

See project prompt / architecture v2 for full pipeline (Prefect, Gemini, Pillow branding, Telegram webhook, Twilio/WAHA broadcast).
