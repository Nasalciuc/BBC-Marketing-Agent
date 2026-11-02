"""
The fact check on a rewritten caption, without AI: every number, airport or airline code, month
and weekday in the new text must already be in the old caption or in what Betty asked. A writer
that "improves" a post by adding a date, a time, a fare or a route is caught here.
"""
from __future__ import annotations

import re
import unicodedata

_NUMBER = re.compile(r"\d+")
_CODE = re.compile(r"(?<![A-Za-z])[A-Z]{2,3}(?![A-Za-z])")     # JFK, CDG, AF, BA
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
MONTHS = {
    "jan": "jan", "january": "jan", "ian": "jan", "ianuarie": "jan",
    "feb": "feb", "february": "feb", "februarie": "feb",
    "mar": "mar", "march": "mar", "martie": "mar",
    "apr": "apr", "april": "apr", "aprilie": "apr",
    "may": "may",                      # not the Romanian "mai": it also means "more" ("mai scurt")
    "jun": "jun", "june": "jun", "iun": "jun", "iunie": "jun",
    "jul": "jul", "july": "jul", "iul": "jul", "iulie": "jul",
    "aug": "aug", "august": "aug",
    "sep": "sep", "sept": "sep", "september": "sep", "septembrie": "sep",
    "oct": "oct", "october": "oct", "octombrie": "oct",
    "nov": "nov", "november": "nov", "noiembrie": "nov",
    "dec": "dec", "december": "dec", "decembrie": "dec",
}
WEEKDAYS = {
    "monday": "mon", "tuesday": "tue", "wednesday": "wed", "thursday": "thu", "friday": "fri",
    "saturday": "sat", "sunday": "sun", "luni": "mon", "marti": "tue", "miercuri": "wed", "joi": "thu",
    "vineri": "fri", "sambata": "sat", "duminica": "sun",
}
# Words that are capitals but not codes in the posts' own vocabulary.
NOT_CODES = frozenset({"OK", "TV", "VIP", "BBC", "AM", "PM", "UK", "US", "USA", "EU"})


def _plain(word: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", word.lower()) if not unicodedata.combining(c))


def facts(text: str) -> set[str]:
    """The checkable facts in a text, normalised: '#6', 'code:JFK', 'month:nov', 'day:fri'."""
    found = {f"#{int(n)}" for n in _NUMBER.findall(text)}
    found |= {f"code:{c}" for c in _CODE.findall(text) if c not in NOT_CODES}
    for word in _WORD.findall(text):
        w = _plain(word)
        if w in MONTHS and not (w == "may" and word.islower()):       # "may" the verb
            found.add(f"month:{MONTHS[w]}")
        elif w in WEEKDAYS:
            found.add(f"day:{WEEKDAYS[w]}")
    return found


def invented(new: str, *sources: str) -> list[str]:
    """The facts of `new` found in none of `sources` — human-readable, sorted."""
    known: set[str] = set()
    for s in sources:
        known |= facts(s)
    return sorted(f.split(":", 1)[-1].lstrip("#") if ":" in f else f.lstrip("#")
                  for f in facts(new) - known)
