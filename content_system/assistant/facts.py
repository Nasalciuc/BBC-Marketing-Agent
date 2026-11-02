"""
The fact check on a rewritten caption, without AI: every number (in digits or in words), airport or
airline code, month and weekday in the new text must already be in the old caption or in Betty's own
message — never in the model's paraphrase of it. A writer that "improves" a post by adding a date, a
time ("half past nine" for "half past eight"), a fare or a route is caught here.
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
# Abbreviations count only capitalised ("Sat 17 Oct"; "sat" is a verb).
WEEKDAY_ABBR = {"Mon": "mon", "Tue": "tue", "Tues": "tue", "Wed": "wed", "Thu": "thu", "Thur": "thu",
                "Thurs": "thu", "Fri": "fri", "Sat": "sat", "Sun": "sun"}
# Numbers written as words, as the house style writes times ("half past eight", "for the sixth time").
# Not "one": it is mostly "one weekend", "one way". Romanian words matter in her message only.
NUMBER_WORDS = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100, "thousand": 1000,
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8,
    "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12, "twentieth": 20, "hundredth": 100,
    "half": "half", "quarter": "quarter", "dozen": 12, "noon": "noon", "midday": "noon", "midnight": "midnight",
    "doi": 2, "doua": 2, "trei": 3, "patru": 4, "cinci": 5, "sase": 6, "sapte": 7, "opt": 8, "noua": 9,
    "zece": 10, "unsprezece": 11, "doisprezece": 12, "douasprezece": 12, "douazeci": 20, "treizeci": 30,
    "suta": 100, "sute": 100, "mie": 1000, "mii": 1000, "jumatate": "half", "sfert": "quarter",
    "pranz": "noon", "miezul": "midnight",
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
        elif w in WEEKDAYS or (w.endswith("s") and w[:-1] in WEEKDAYS):  # "Friday", "Fridays"
            found.add(f"day:{WEEKDAYS[w if w in WEEKDAYS else w[:-1]]}")
        elif word in WEEKDAY_ABBR:
            found.add(f"day:{WEEKDAY_ABBR[word]}")
        elif w in NUMBER_WORDS:
            n = NUMBER_WORDS[w]
            found.add(f"#{n}" if isinstance(n, int) else f"time:{n}")
    return found


def invented(new: str, *sources: str) -> list[str]:
    """The facts of `new` found in none of `sources` — human-readable, sorted."""
    known: set[str] = set()
    for s in sources:
        known |= facts(s)
    return sorted(f.split(":", 1)[-1] if ":" in f else f.lstrip("#") for f in facts(new) - known)
