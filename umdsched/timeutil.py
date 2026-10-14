"""Parsing and formatting of days and times.

Times are stored as minutes after midnight; days as ints (0 = Monday).
"""

from __future__ import annotations

import re

DAY_CODES = ["M", "Tu", "W", "Th", "F", "Sa", "Su"]
ICS_DAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]

_DAY_RE = re.compile(r"Tu|Th|Sa|Su|M|W|F|T|R|S|U", re.IGNORECASE)
_DAY_ALIASES = {
    "m": 0, "tu": 1, "t": 1, "w": 2, "th": 3, "r": 3, "f": 4,
    "sa": 5, "s": 5, "su": 6, "u": 6,
}
_FULL_DAY_NAMES = {
    "mon": 0, "monday": 0, "tue": 1, "tues": 1, "tuesday": 1,
    "wed": 2, "wednesday": 2, "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
    "fri": 4, "friday": 4, "sat": 5, "saturday": 5, "sun": 6, "sunday": 6,
}
_TIME_RE = re.compile(r"^\s*(\d{1,2})(?::(\d{2}))?\s*([ap])?\.?\s*m?\.?\s*$", re.IGNORECASE)


def parse_days(text: str) -> tuple[int, ...]:
    """Parse a day string such as "MWF", "TuTh", "MTuWThF", "TR" or "mon,wed".

    Single-letter "T" is Tuesday and "R" is Thursday (registrar style).
    """
    text = text.strip()
    if not text:
        return ()
    parts = re.split(r"[\s,/]+", text.lower())
    if all(p in _FULL_DAY_NAMES for p in parts if p):
        return tuple(sorted({_FULL_DAY_NAMES[p] for p in parts if p}))
    compact = re.sub(r"[\s,/]+", "", text)
    days: set[int] = set()
    pos = 0
    while pos < len(compact):
        m = _DAY_RE.match(compact, pos)
        if not m:
            raise ValueError(f"can't parse days {text!r} (use e.g. MWF, TuTh, MTuWThF)")
        days.add(_DAY_ALIASES[m.group(0).lower()])
        pos = m.end()
    return tuple(sorted(days))


def format_days(days) -> str:
    return "".join(DAY_CODES[d] for d in sorted(days))


def parse_time(text: str, *, assume_pm_for_small: bool = True) -> int:
    """Parse "10:00am", "2pm", "14:30", "9" into minutes after midnight.

    A bare hour from 1 to 7 with no am/pm is treated as PM, since nobody
    means 3 AM when describing a class schedule.
    """
    m = _TIME_RE.match(text)
    if not m:
        raise ValueError(f"can't parse time {text!r} (use e.g. 9:30, 9:30am, 14:00, 2pm)")
    hour, minute, ampm = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
    if minute >= 60 or hour > 24:
        raise ValueError(f"invalid time {text!r}")
    if ampm:
        if not 1 <= hour <= 12:
            raise ValueError(f"invalid time {text!r}")
        hour = hour % 12 + (12 if ampm == "p" else 0)
    elif assume_pm_for_small and 1 <= hour <= 7:
        hour += 12
    return min(hour * 60 + minute, 24 * 60)


def parse_time_range(text: str) -> tuple[int, int]:
    """Parse "11:00-14:00" or "11am-2pm" into a (start, end) pair."""
    parts = re.split(r"\s*(?:-|–|to)\s*", text.strip())
    if len(parts) != 2:
        raise ValueError(f"can't parse time range {text!r} (use e.g. 11:00-14:00)")
    start, end = parse_time(parts[0]), parse_time(parts[1])
    # "11-1" style: end before start means the end is in the afternoon.
    if end <= start and end < 12 * 60:
        end += 12 * 60
    if end <= start:
        raise ValueError(f"time range {text!r} ends before it starts")
    return start, end


def parse_block(text: str) -> tuple[tuple[int, ...], int, int]:
    """Parse a busy block like "MWF 12:00-13:00", "Tu 3pm-5pm" or "F" (all day)."""
    text = text.strip()
    m = re.match(r"^([A-Za-z,/]+)\s*(.*)$", text)
    if not m:
        raise ValueError(f"can't parse block {text!r} (use e.g. 'MWF 12:00-13:00')")
    days = parse_days(m.group(1))
    if not m.group(2).strip():
        return days, 0, 24 * 60
    start, end = parse_time_range(m.group(2))
    return days, start, end


def format_time(minutes: int | None, *, short: bool = False) -> str:
    if minutes is None:
        return "TBA"
    hour, minute = divmod(minutes, 60)
    suffix = "am" if hour < 12 or hour == 24 else "pm"
    h12 = hour % 12 or 12
    if short and minute == 0:
        return f"{h12}{suffix}"
    return f"{h12}:{minute:02d}{suffix}"


def format_duration(minutes: float) -> str:
    minutes = int(round(minutes))
    if minutes < 60:
        return f"{minutes}m"
    hours, rem = divmod(minutes, 60)
    return f"{hours}h{rem:02d}m" if rem else f"{hours}h"
