"""What Betty reads: the menu, the help, the fixed replies. Romanian; the posts stay in English."""
from __future__ import annotations

MENU_WEEK = "📋 Săptămâna"
MENU_TODO = "✅ De aprobat"
MENU_IDEA = "💡 Idee"
MENU_HELP = "❓ Ajutor"
MENU = (MENU_WEEK, MENU_TODO, MENU_IDEA, MENU_HELP)

IDEA_MARK = "💡 Idee nouă"          # the first line of the idea prompt: a reply to it is an idea

STATUS = {
    "in_review": "așteaptă decizia",
    "approved": "aprobată",
    "rejected": "respinsă",
    "locked": "blocată",
    "failed": "s-a oprit la jumătate",
    "rendered": "doar randată",
    "not started": "netrimisă",
}

WEEKDAYS = {   # Romanian (with and without diacritics) and English → strftime("%a")
    "luni": "Mon", "marti": "Tue", "marți": "Tue", "miercuri": "Wed", "joi": "Thu", "vineri": "Fri",
    "sambata": "Sat", "sâmbătă": "Sat", "sîmbătă": "Sat", "duminica": "Sun", "duminică": "Sun",
    "monday": "Mon", "tuesday": "Tue", "wednesday": "Wed", "thursday": "Thu", "friday": "Fri",
    "saturday": "Sat", "sunday": "Sun", "mon": "Mon", "tue": "Tue", "wed": "Wed", "thu": "Thu",
    "fri": "Fri", "sat": "Sat", "sun": "Sun",
}

WELCOME = (
    "Bună, Betty! Sunt asistentul postărilor.\n\n"
    "Postările săptămânii vin aici, fiecare cu butoanele ei. Îmi poți scrie ca unui coleg: "
    "„ce postăm vineri?”, „fă textul de la Cape Town mai scurt”, „aprob toate”.\n\n"
    "Eu nu aprob și nu public nimic: decizia e mereu butonul pe care îl apeși tu."
)

HELP = (
    "❓ Cum lucrăm\n\n"
    "• Fiecare postare are ✅ Approve · ❌ Reject · ✏️ Edit caption. Apasă și gata.\n"
    "• Ca să schimbi textul cu cuvintele tale: răspunde la postare, de ex. „mai scurt, fără partea cu prețul”. "
    "Primești o versiune nouă, cu butoanele ei.\n"
    "• Ca să pui exact textul tău: ✏️ Edit caption, apoi răspunde la mesajul meu cu textul.\n"
    "• Întreabă orice despre postări: „ce postăm joi?”, „care așteaptă?”.\n"
    "• „aprob toate” → îți arăt un card cu postările și versiunile lor; aprobă abia butonul de pe card.\n"
    "• Poza, tariful, ruta, ziua sau publicarea → îi trimit cererea lui Scaler și îți spun că a ajuns.\n\n"
    f"Meniul de jos: {MENU_WEEK} · {MENU_TODO} · {MENU_IDEA} · {MENU_HELP}"
)

IDEA_PROMPT = f"{IDEA_MARK}\nRăspunde la acest mesaj cu ideea ta. O pun în lista pentru planul săptămânii."
IDEA_SAVED = "💡 Am pus ideea în listă pentru planul săptămânii. Mersi!"
NO_POSTS = "Încă nu am postări trimise pe Telegram."
NOTHING_WAITING = "Nicio postare nu așteaptă decizia acum. 🎉"
OVER_CAP = ("Pentru azi am ajuns la limita de cost a AI-ului, deci nu pot răspunde la text până mâine. "
            "Meniul de jos și butoanele de pe postări merg în continuare.")
AI_DOWN = ("Nu pot răspunde acum (AI-ul nu e disponibil). Meniul de jos și butoanele de pe postări "
           "merg în continuare; încearcă din nou peste câteva minute.")
BUSY = "Încă lucrez la mesajul tău de dinainte — trimite-l pe acesta din nou peste câteva secunde."
STILL_WORKING = "⏳ Mai durează puțin…"
TOO_LONG = ("M-am oprit: cererea mi-a cerut prea mulți pași dintr-odată. Ce am făcut deja rămâne; "
            "spune-mi pe rând ce mai e de făcut.")
DB_DOWN = "⚠️ Nu ajung la baza de date acum — încearcă din nou peste un minut."


def menu_keyboard() -> dict:
    return {"keyboard": [[{"text": MENU_WEEK}, {"text": MENU_TODO}], [{"text": MENU_IDEA}, {"text": MENU_HELP}]],
            "resize_keyboard": True, "is_persistent": True}
