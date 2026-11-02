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

### Photos from a brief (`photos`, PR-4)

A post can carry a short brief instead of a photo file; `photos` makes the photo from it:

```toml
[post.image]
scene   = "Table Mountain at golden hour over Clifton, the Atlantic below"   # the defining moment
vantage = "a villa terrace above Clifton beach"                              # where the client is
device  = "the edge of a teak sun lounger with a folded towel"               # the foreground, from there
time    = "golden hour"                                                      # optional
people  = "none"                                                             # or "few": small, left, out of focus
```

`railway run python -m content_system photos weeks/2026-W43.toml [--post 2 4] [--again] [--candidates 3]`

- Per post with a brief and no photo yet: 3 candidates from `gemini-3.1-flash-image` (9:16, 2K), each
  cropped to 1080×1920 and checked without AI. A candidate is refused if its 9:16 crop is under 540 px
  wide, if it is blank, or if it is far too dark or bright. A soft image or a source that isn't 9:16
  is only noted.
- Then `claude-sonnet-5-5` looks at it against the rules:
  - one single photograph (no collage, nothing pasted);
  - no crowd;
  - people only if the brief allows them, and then small, on the left, out of focus;
  - no text or logos;
  - the brief's scene, vantage and foreground;
  - a calm upper part for the logo and headline;
  - no generation errors;
  - a score of 7/10 or more.

  Whether it passes is decided in the code from those answers, read strictly: a missing answer, or
  one of the wrong type, fails.
- Every run keeps what it tried in its own folder, `<photos>/.candidates/<photo>/<UTC time>/`
  (`1.jpg`, `2.jpg` … and `qc.json`, the report); nothing from an earlier run is overwritten.
  `.candidates/` is ignored by git. The best passing candidate becomes `<photos>/<photo>.jpg`,
  written beside it and renamed into place.
- A later candidate is told why the earlier one failed: what was wrong with the picture, not API errors.
- A photo already there is kept. `--again` makes a new one and moves the old one into that run's
  folder as `previous.jpg`, so `--again` twice still keeps the original.
- Calls are bounded: a Gemini request times out after 180 s and is retried on 429/5xx (3 attempts).
  A wrong key or model name (401/403/404/400) stops the run at once instead of paying for every
  candidate.
- `check` refuses a week whose foreground device repeats, compared by its words ("two empty armchairs"
  repeats "an empty armchair"; "the teak sun-lounger" repeats "a teak sun lounger"). It also refuses
  two posts with a brief that share a photo name.
- Settings: `CONTENT_IMAGE_MODEL`, `CONTENT_IMAGE_SIZE` (1K/2K/4K), `CONTENT_VISION_MODEL`,
  `CONTENT_IMAGE_CANDIDATES`; keys `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`.
- Then, as always: look at the photos, `render`, look at `_preview.jpg`, `send`.

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

## The week's plan (`content_system/planning`, PR-3)

Before the posts are made, Betty approves **what** the week will be about. Nothing here makes a
photo, a campaign or a post: that comes after her ✅, and each post still gets its own ✅ later.

```
plan --week W --send:  research (web search) → choice → one draft per post → manifest check → Telegram
Betty:                 ❌ Scot / ↩️ Pun-o înapoi per post · ✅ Aprob planul · 🔁 Vreau alt plan
build --week W:        the approved plan → weeks/W.toml → photos → render      then: send weeks/W.toml
```

| Command | Needs | Does |
|---|---|---|
| `railway run python -m content_system plan --week 2026-W47` | `ANTHROPIC_API_KEY` (and `CONTENT_DB_URL` for Betty's ideas) | researches and writes `output/content/2026-W47/plan-<id>.toml` — read it, nothing is sent |
| `railway run python -m content_system plan --week 2026-W47 --send` | + `CONTENT_DB_URL`, bot token, `TELEGRAM_CHAT_ID` | a new plan, stored in `content.plans`, sent to Betty; older open plans of the week stop working |
| `railway run python -m content_system plan --week 2026-W47 --status` | `CONTENT_DB_URL` | the week's plans and what Betty decided |
| `railway run python -m content_system build --week 2026-W47 [--replace] [--no-photos]` | `CONTENT_DB_URL`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY` | the approved plan without the posts she took out → `weeks/2026-W47.toml`, its photos, the render |

How a plan is made (Claude Sonnet 5.5, `CONTENT_PLAN_MODEL`):

1. **Research, with web search** (up to `CONTENT_PLAN_SEARCHES`, default 10, $10 per 1000): about 12
   candidates (events 2–10 weeks ahead, destinations in season, airline/lounge news, hotels; Betty's
   new ideas first), each with its facts and the page of each fact. A fact whose page was not among
   the pages the searches actually returned is dropped; a candidate left with fewer than two facts,
   or an event already over, is dropped.
2. **Choice:** one candidate per publishing day (`CONTENT_PLAN_DAYS`, default Wed–Sun) with a style.
   Checked in code, asked again with the reasons (3 tries): the week mix (≤60% with a fare, never
   three in a row, a style at most twice), a content type at most twice and at least three types, a
   priced style only for a destination the pricing engine can price, an event not over on its day.
3. **Drafts:** per post the text, the design fields of its style and the photo brief. Checked in
   code, written again once with the reasons: the manifest fields, the caption rules (no `$`, no
   `_ * \` [`, the length), a fare the pricing engine gives for the post's own destination, the
   foreground device not repeated, and the fact check of the assistant — **every number, airport
   code, month and weekday in the text must be in the research** (or be the post's own day).
4. **The whole week** goes through the manifest loader (`load_week`) before it is offered.

On Telegram, Betty gets a header, one message per post (day, style, headline, text, the photo in
words, up to three source links) and a last card. ✅ is refused when the posts left would break the
week mix ("pune una înapoi sau cere alt plan"). On ✅ her ideas in the plan are marked `planned` and
Scaler's chat (`CONTENT_OWNER_CHAT_ID`) gets the `build` command; on 🔁, the `plan … --send` command.
Taps are claimed per Telegram update (a repeated delivery does nothing) and a closed plan's buttons
answer "Planul acesta e deja închis".

Before the first `plan --send`: migration `013_plans.sql` in the SQL Editor (after 012). `plan` and
`build` check that `content.plans` and `content.ideas` exist.

```sql
select id, week, status, jsonb_array_length(posts) as posts, dropped, created_at, decided_at
  from content.plans order by created_at desc limit 10;                                       -- the plans
```
