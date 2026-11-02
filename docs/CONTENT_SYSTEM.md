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
