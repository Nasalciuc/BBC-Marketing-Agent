# Pattern-uri extrase din repo-urile de referință

> Sursă: `ghostwriter/` (developmentv2) + `tiktok-to-youtube-bot/`
> **Zero credențiale copiate** — doar arhitectură și flow-uri.

---

## RAPORT EXTRACȚIE COD

### DIN GHOSTWRITER → BBC

| Fișier sursă | Ce extrag | Unde merge în BBC | Adaptări |
|---|---|---|---|
| `image_quality_enhancer.py` | `smart_resize`, `upscale`, `enhance_quality`, RGBA→RGB, EXIF | `services/image_enhancer.py` | Eliminat async download FMF; dimensiuni BBC WhatsApp 1200×628 |
| `simple_workflow_bot_fixed.py` | `show_preview()`, `pending_posts`, approve callback | `services/telegram_client.py` (S4) | Deal card în loc de URL scrape; fără token hardcodat |
| `social_media_posting.py` | `DEMO_MODE`, `post_to_all()`, error types | `services/waha_client.py` (S6) | Doar pattern facade multi-channel |
| `config.py` | `Settings` + `load_dotenv` | Deja în `config.py` (pydantic-settings, mai bun) | — |
| `mvp_caption_agent.py` | AI → rule-based fallback | `services/gemini_client.py` (S3) | Prompt BBC brand voice |
| `agents.py` | CrewAI multi-agent | Referință only — nu folosim CrewAI acum | — |

### DIN TIKTOK-TO-YOUTUBE-BOT → BBC

| Fișier sursă | Ce extrag | Unde merge în BBC | Adaptări |
|---|---|---|---|
| `handlers/review.py` | Review card + approve/edit/skip | `webhook.py` (S4) | Video → branded deal JPEG |
| `keyboards.py` | Prefix callbacks (`rv:ok:`), `InlineKeyboardBuilder` | `keyboards.py` sau inline în webhook | Butoane: ✅ Approve, ❌ Reject |
| `middlewares.py` | `AdminOnlyMiddleware` | `webhook.py` | Whitelist Scaler + Mason |
| `services/pipeline.py` | State machine + claim-next loop | `discover.py` / Prefect tasks (S5) | Pași BBC: discover→price→brand→notify |
| `main.py` | Bot + background worker same process | Nu — Railway cron + webhook separat | — |
| `config.py` | Frozen dataclass env | Deja avem pydantic `Settings` | — |

### CE NU COPIEM

- Token-uri, API keys, credențiale
- `.env` cu valori reale din ghostwriter
- Cod FMF (fmf.md scrapers, prompt-uri moldovenești)
- Cod TikTok/YouTube (yt-dlp, OAuth, cookies)

---

## Telegram Approval

### Preview message (ghostwriter `show_preview`)

1. Procesează conținutul (URL → scrape → caption → images).
2. Stochează în `pending_posts[user_id]`.
3. Trimite foto + caption + `InlineKeyboardMarkup` (Approve / Cancel).
4. La callback: postează sau anulează, șterge keyboard.

**BBC adaptare:** trimite `generate_branded_image()` bytes ca photo; caption = event + route + price; callback = approve/reject deal ID.

### Review card (tiktok `send_review_card`)

1. Trimite la `settings.primary_admin` (primul admin din listă).
2. Fallback chain: `send_video` → `send_document` → text only.
3. Salvează `review_chat_id` + `review_message_id` în DB.
4. Callback prefixes: `rv:ok:{id}`, `rv:ed:{id}`, `rv:no:{id}`.
5. Approve → schimbă status DB → pipeline continuă; Skip → șterge fișier local.

**BBC adaptare:** deal JPEG + HTML caption; approve → Supabase status `approved` → broadcast queue.

### InlineKeyboard layout (tiktok `keyboards.py`)

- Constante scurte pentru `callback_data` (≤64 bytes): `rv:ok:123`.
- `InlineKeyboardBuilder` + `.adjust(cols)` pentru layout.
- Funcții dedicate per ecran: `review_card_kb(video_id)`.

**BBC layout propus:**

```text
[ ✅ Approve deal ] [ ❌ Reject ]
[ ✏️ Edit caption  ]              ← optional S4+
```

### Admin middleware (tiktok `middlewares.py`)

- `AdminOnlyMiddleware(admins: frozenset[int])` pe outer message + callback.
- Non-admin: alert popup (callback) sau reply text (message); handler nu rulează.

---

## Pipeline orchestration (tiktok `services/pipeline.py`)

- **State machine:** DISCOVERED → DOWNLOADING → AWAITING_REVIEW | READY → UPLOADING → DONE | FAILED.
- **Loop:** tick la 5s; `claim_next(from, to)` atomic din SQLite.
- **Review mode:** după download → `on_review_needed()` callback; auto mode → READY direct.
- **Retry:** quota exceeded → revert READY + sleep 60s; `mark_failed()` per stage.

**BBC pipeline (Prefect, S5):**

```text
discover_deals → calculate_prices → fetch_unsplash → brand_image → store_supabase → notify_telegram
                                                              ↓
                                                    AWAITING_REVIEW (optional)
                                                              ↓
broadcast_approved → twilio / waha / sheets
```

---

## Image processing (ghostwriter → BBC)

Funcții migrate în `services/image_enhancer.py`:

| GhostWriter | BBC |
|---|---|
| `_smart_resize()` | `smart_resize()` |
| `_upscale_image()` | `upscale_if_needed()` |
| `_enhance_quality()` | `enhance_quality()` |
| RGBA paste on white | `ensure_rgb()` |
| — | `ImageOps.exif_transpose()` |
| `download_and_enhance_images()` async | Viitor: `unsplash_client.py` |

Branding engine folosește `prepare_image()` pentru background cover + light enhance înainte de overlay-uri.
