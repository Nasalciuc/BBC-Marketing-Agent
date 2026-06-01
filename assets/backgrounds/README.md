# Fotografii fundal — test branding / AI

Pune aici **stock photos reale** (JPG/PNG) pentru teste locale, fără Gemini image gen.

## Unde pui fișierele

```
assets/backgrounds/
├── f1_monaco_test.png      ← exemplu (F1 Monaco)
├── wimbledon_court.jpg     ← adaugă tu
└── your_photo.jpg          ← orice eveniment
```

## Test rapid cu Playwright branding

```powershell
python -c "
from services.branding_engine import generate_branded_image
from pathlib import Path
bg = 'assets/backgrounds/f1_monaco_test.png'  # schimbă numele
out = Path('output/branding_test/from_custom_bg.jpg')
out.parent.mkdir(parents=True, exist_ok=True)
out.write_bytes(generate_branded_image(
    'F1 Monaco Grand Prix', 'JFK -> NCE', '$2,069', bg,
    caption='Fly business class to Monte Carlo',
))
print('Saved:', out, out.stat().st_size, 'bytes')
"
```

## Pipeline complet (Gemini gen + branding)

În `scripts/test_real_pipeline.py`, poți forța un fundal local setând manual `event['bg_path']` sau copiază foto aici și folosește scriptul de mai sus.

## Recomandări

- **Rezoluție:** minim 1200×628 (landscape WhatsApp)
- **Format:** JPG sau PNG
- **Conținut:** business class, orașe, evenimente — fără text/watermark pe imagine

## Git

Fișierele din acest folder sunt **gitignored** (binare mari). Rămân doar local / pe Railway volume.
