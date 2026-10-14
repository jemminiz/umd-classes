"""UMD term ids (e.g. 202608 = Fall 2026) and approximate term dates."""

from __future__ import annotations

import datetime as dt
import re

SEASONS = {"01": "Spring", "05": "Summer", "08": "Fall", "12": "Winter"}
_SEASON_CODES = {
    "spring": "01", "sp": "01", "s": "01",
    "summer": "05", "su": "05",
    "fall": "08", "fa": "08", "f": "08",
    "winter": "12", "wi": "12", "w": "12",
}


def default_term(today: dt.date | None = None) -> str:
    """The term students are most likely planning for on a given date.

    Registration for fall opens in spring and registration for spring opens
    in late October, so: Jan-Feb -> this spring, Mar-Sep -> this fall,
    Oct-Dec -> next spring.
    """
    today = today or dt.date.today()
    if today.month <= 2:
        return f"{today.year}01"
    if today.month <= 9:
        return f"{today.year}08"
    return f"{today.year + 1}01"


def parse_term(text: str | None, today: dt.date | None = None) -> str:
    """Accept "202608", "fall2026", "Fall 2026", "f26", "spring" (nearest upcoming)."""
    if not text:
        return default_term(today)
    t = text.strip().lower().replace(" ", "").replace("-", "")
    if re.fullmatch(r"\d{4}(01|05|08|12)", t):
        return t
    m = re.fullmatch(r"([a-z]+)(\d{2}|\d{4})?", t)
    if m and m.group(1) in _SEASON_CODES:
        code = _SEASON_CODES[m.group(1)]
        if m.group(2):
            year = int(m.group(2))
            year = year + 2000 if year < 100 else year
        else:
            today = today or dt.date.today()
            year = today.year
            # Pick the next occurrence of that season.
            if int(code) < today.month - 1:
                year += 1
        return f"{year}{code}"
    raise ValueError(f"can't parse term {text!r} (use e.g. 202608, fall2026, spring27)")


def term_name(term: str) -> str:
    return f"{SEASONS.get(term[4:], term[4:])} {term[:4]}"


def approx_term_dates(term: str) -> tuple[dt.date, dt.date]:
    """Rough first/last day of classes, used only for calendar export.

    These are approximations; pass --term-start/--term-end for exact dates.
    """
    year, season = int(term[:4]), term[4:]

    def nth_weekday(month: int, weekday: int, n: int) -> dt.date:
        d = dt.date(year, month, 1)
        d += dt.timedelta(days=(weekday - d.weekday()) % 7)
        return d + dt.timedelta(weeks=n - 1)

    if season == "08":  # Fall: Monday before Labor Day through ~Dec 10
        labor_day = nth_weekday(9, 0, 1)
        return labor_day - dt.timedelta(days=7), dt.date(year, 12, 10)
    if season == "01":  # Spring: last Monday of January through ~May 11
        last_monday = nth_weekday(1, 0, 4)
        if (last_monday + dt.timedelta(weeks=1)).month == 1:
            last_monday += dt.timedelta(weeks=1)
        return last_monday, dt.date(year, 5, 11)
    if season == "05":
        return dt.date(year, 6, 1), dt.date(year, 8, 14)
    return dt.date(year - 1, 12, 26), dt.date(year, 1, 23)
