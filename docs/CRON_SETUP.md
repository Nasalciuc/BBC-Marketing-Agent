# Railway Cron Setup — Autonomous Agent

Crons se configurează în Railway dashboard (nu în cod):

## Cron 1: Weekly Content Generation
- Schedule: `0 14 * * 5` (Friday 14:00 UTC)
- Command: `python scripts/autonomous_agent.py`

## Cron 2: Monday Broadcast
- Schedule: `0 10 * * 1` (Monday 10:00 UTC)
- Command: `python broadcast.py`

## Manual trigger:
Directorul scrie pe Telegram: "generează postări" → rulează imediat.
