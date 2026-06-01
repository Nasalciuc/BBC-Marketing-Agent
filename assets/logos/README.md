# Logo-uri BBC — BuyBusinessClass

Pune fișierele PNG ale companiei **direct în acest folder**.

## Fișiere recomandate

| Copiază fișierul tău ca… | Format sursă | Folosit pentru |
|--------------------------|--------------|----------------|
| `bbc_logo_horizontal.png` | LOGO_BBC_1200X300 | **Bannere deal** (landscape 1200×628) — prioritate |
| `bbc_logo_square.png` | LOGO_BBC_1200X1200 | Variantă pătrată (story / pătrat) |
| `bbc_logo_white.png` | Logo alb pe fundal închis | Opțional, dacă ai variantă dedicată |

Engine-ul caută logo-urile în această ordine; primul fișier găsit este folosit.

## Exemplu (PowerShell)

```powershell
Copy-Item "C:\Downloads\LOGO_BBC_1200X300.png" "assets\logos\bbc_logo_horizontal.png"
Copy-Item "C:\Downloads\LOGO_BBC_1200X1200.png" "assets\logos\bbc_logo_square.png"
```

## Verificare

```powershell
python scripts/test_branding_visual.py
```

Dacă logo-ul e încărcat, în log vei vedea: `Logo loaded: bbc_logo_horizontal.png`  
Fără logo → fallback text „BuyBusinessClass.com”.

## Notă Git

PNG-urile din `assets/logos/` sunt ignorate în git (`.gitignore`).  
Pe Railway: upload manual, volume, sau variabilă de mediu — logo-urile trebuie prezente în container la path-ul de mai sus.
