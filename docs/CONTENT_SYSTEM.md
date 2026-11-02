# Content system (LangGraph)

The weekly posts as a durable workflow. A week is a TOML manifest in `weeks/`; each post is a
LangGraph thread `post:<campaign_id>` checkpointed in Postgres (schema `content`). Betty's
Telegram taps resume the thread from the webhook, hours or days later.

```
render → upload → publish → review ⏸ → approved | rejected
                     ▲          │
                     └ edited ◄─┘  (reply with a new caption → v2, v3 …)
```

Nothing here sends to clients: approved posts are `status = approved` in `public.campaigns`.
Publishing to the WhatsApp channel is a separate step (broadcast stays off: `BROADCAST_ENABLED=false`).

## A week

1. Photos in `assets/curated/week_YYYY_MM_DD/` (1080×1920, 9:16), named as in the manifest.
2. `weeks/2026-W43.toml` — copy `weeks/2026-W42.toml` (the reference week) and change it.
   Campaign ids are `<week>-<id_tag>-00n`; the first post is 001.
3. Commands:

| Command | Needs | Does |
|---|---|---|
| `python -m content_system check weeks/2026-W43.toml` | nothing | manifest, captions, week mix, photos |
| `python -m content_system render weeks/2026-W43.toml` | Playwright | posts + captions + `_preview.jpg` in `output/content/<week>/` |
| `railway run python -m content_system send weeks/2026-W43.toml` | all variables | render, upload, `campaigns` rows, Telegram review |
| `railway run python -m content_system status weeks/2026-W43.toml` | `CONTENT_DB_URL` | where each post is |
| `railway run python -m content_system decide <id> approve\|reject` | `CONTENT_DB_URL`, bot token | same as the buttons |
| `railway run python -m content_system db-setup` | `CONTENT_DB_URL` | LangGraph tables + checks |

`send` is safe to re-run: posts waiting for review are skipped, a post whose last run failed
continues where it stopped, and an id that exists in `campaigns` without a content-system thread
is refused (change `id_tag`).

## Review on Telegram

Each post arrives with ✅ Approve · ❌ Reject · ✏️ Edit caption and the line `📋 <id> · v<n>`.
✏️ sends a message to reply to; a reply to it, or to any version of the post, is the new caption —
the text alone is enough, the fare line and the contact line are added. The new version arrives as
a new message and the old one's buttons are retired; a tap on an old version's buttons is refused.
Approving stores exactly the caption of the version that was approved.

## Variables (Railway)

| Variable | Value |
|---|---|
| `CONTENT_ENABLED` | `true` to let the webhook route `cs:` buttons and replies (default `false`) |
| `CONTENT_DB_URL` | Supabase **Session pooler** string, port 5432, user `content_app.<project-ref>` (never the transaction pooler 6543, never the postgres user) |
| `CONTENT_DB_POOL_MAX` | optional, default 4 |

## Checks in SQL (project bbc-marketing-agent)

```sql
select campaign_id, status, scheduled_day, design_style, review_message_id, left(caption, 40)
  from public.campaigns where campaign_id like '2026-W43-%' order by campaign_id;
select split_part(key, ':', 1) as effect, status, count(*) from content.effects group by 1, 2 order by 1;
select * from content.leases;                       -- normally empty
```

## Guarantees and their limits

- One decision at a time per post (`content.leases`; a lease left by a dead process frees itself
  after 10 minutes).
- Each upload and each review / status message runs once per key (`content.effects`). A request
  Telegram refused (4xx) leaves no trace. If Telegram accepted a message but its answer was lost
  (timeout, 5xx), the retry sends a second copy: same post, same version, same text — its buttons
  and replies work exactly like the first, because every message carries `📋 <id> · v<n>`.
- A decision that failed half-way (database or Telegram down through every retry) counts as not
  taken: the same tap again completes it, a different tap replaces it, and a write the failed
  run did make is reported ("Already approved — nothing changed"), never hidden. A version number
  is never reused for a different text.
- A campaign that is no longer `draft`/`editing` is never overwritten: the code checks it, and the
  database policies of `content_app` (migrations/011) refuse it as well. Images are uploaded under
  a new name (`vertical-<hash>.jpg`) and never overwrite a file.

## Betty's assistant (`content_system/assistant`, off until `CONTENT_ASSISTANT_ENABLED=true`)

A chat with buttons in the same Telegram bot. The AI never approves, rejects or publishes.

| Betty sends | What happens | AI? |
|---|---|---|
| a tap on a post's ✅ / ❌ / ✏️ | as above (engine, lease, version) | no |
| 📋 Săptămâna · ✅ De aprobat · 💡 Idee · ❓ Ajutor (the menu at the bottom), `/start`, `/help` | the week, a "✅ Aprob …" card, an idea prompt, the help | no |
| a reply to "✏️ <id> · v<n>" | that exact text is the new caption | no |
| a reply to "💡 Idee nouă" | saved in `content.ideas` | no |
| a reply to a post ("mai scurt…") or any other text | the assistant, in the background (the webhook answers at once) | yes |

The assistant (LangChain `create_agent`, Claude Haiku 5.5, Sonnet 5.5 when Haiku fails, ≤8 model
calls and ≤6 tool calls per message, the chat's last 30 messages in the thread `chat:<chat_id>`)
has nine tools: `week_overview`, `find_post`, `show_post`, `rewrite_caption`, `use_exact_text`,
`propose_approve`, `propose_reject`, `save_idea`, `ask_scaler`.

- **A caption change is a new version.** `rewrite_caption` asks the writer (Sonnet 5.5) for a new
  body, checks it — the caption rules, and no number, airport code, month or weekday that is in
  neither the post nor her request — writes once more with the reasons if it fails, and only then
  makes v+1 through `engine.decide("edit")`, with its own buttons. `use_exact_text` takes text that
  is, word for word, in her message.
- **Approvals are cards.** `propose_approve` sends "✅ Aprob …?" with one button per post and one for
  all; each holds the exact versions shown (`content.proposals`). A tap decides each post on its
  version (a post edited since is refused, with the reason); any tap closes the card; a card older
  than 48 h decides nothing. `send` posts such a card for the whole week ("Le aprobi deodată?").
- **What it can't do** (the image, the fare, the route, the day, publishing) goes to Scaler's chat
  (`ask_scaler`, once per message) and Betty is told.
- **Limits:** above `CONTENT_ASSISTANT_DAILY_USD` (default 3 $, counted in `content.usage`), or when
  both models fail, Betty gets a short note and the menu; the buttons keep working.
- With the assistant on, the old free-text conversation (`handlers/conversation.py`) is no longer
  reached, and a reply to a post is not taken as its caption any more (only a reply to ✏️ is).

### Variables (Railway, web service)

| Variable | Value |
|---|---|
| `CONTENT_ASSISTANT_ENABLED` | `true` to turn it on (needs `CONTENT_ENABLED=true`) |
| `ANTHROPIC_API_KEY` | the key the models use |
| `TELEGRAM_ADMIN_IDS` | Betty's and Scaler's Telegram ids (`/start` from someone else answers with their id) |
| `TELEGRAM_CHAT_ID` | Betty's chat: the posts and the cards go there |
| `CONTENT_OWNER_CHAT_ID` | Scaler's chat: what the assistant can't do goes there |
| `CONTENT_ASSISTANT_DAILY_USD` | optional, default `3` |

Before turning it on: migration `012_assistant.sql` in the SQL Editor (after 011), then
`railway run python -m content_system db-setup` — with the assistant on it also checks the 012 tables.

```sql
select tablename from pg_tables where schemaname = 'content' and tablename in ('proposals', 'ideas', 'usage');  -- 3 rows
select day, model, calls, round(cost_usd, 4) from content.usage order by day desc, model;                        -- spend per day
select created_at, text from content.ideas where status = 'new' order by created_at desc;                         -- Betty's ideas
```
