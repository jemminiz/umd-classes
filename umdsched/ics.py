"""Export a schedule as an iCalendar (.ics) file of weekly recurring events."""

from __future__ import annotations

import datetime as dt

from umdsched.scheduler import Schedule
from umdsched.timeutil import ICS_DAYS

VTIMEZONE = """BEGIN:VTIMEZONE
TZID:America/New_York
BEGIN:DAYLIGHT
TZOFFSETFROM:-0500
TZOFFSETTO:-0400
TZNAME:EDT
DTSTART:19700308T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:-0400
TZOFFSETTO:-0500
TZNAME:EST
DTSTART:19701101T020000
RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU
END:STANDARD
END:VTIMEZONE"""


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def schedule_to_ics(s: Schedule, start: dt.date, end: dt.date, term_label: str = "") -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    # UNTIL must be UTC when DTSTART has a TZID; 03:59Z the next day is late evening Eastern.
    until = f"{end + dt.timedelta(days=1):%Y%m%d}T035959Z"
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//umd-sched//EN", "CALSCALE:GREGORIAN", *VTIMEZONE.splitlines()]
    for o in s.options:
        for idx, m in enumerate(o.timed):
            # First day on/after the term start that this meeting happens.
            first = min(start + dt.timedelta(days=(d - start.weekday()) % 7) for d in m.days)
            t0 = dt.datetime.combine(first, dt.time(m.start // 60, m.start % 60))
            t1 = dt.datetime.combine(first, dt.time(m.end // 60, m.end % 60))
            kind = "" if m.kind == "Lecture" else f" {m.kind}"
            summary = f"{o.course_id}{kind}"
            desc = "\n".join(
                [o.course.title, f"Section {o.section_ids[0]}", ", ".join(o.instructors) or "Instructor TBA"]
                + ([term_label] if term_label else [])
            )
            lines += [
                "BEGIN:VEVENT",
                f"UID:{o.course_id}-{o.section_ids[0]}-{idx}-{start:%Y%m%d}@umd-sched",
                f"DTSTAMP:{stamp}",
                f"DTSTART;TZID=America/New_York:{t0:%Y%m%dT%H%M%S}",
                f"DTEND;TZID=America/New_York:{t1:%Y%m%dT%H%M%S}",
                f"RRULE:FREQ=WEEKLY;BYDAY={','.join(ICS_DAYS[d] for d in m.days)};UNTIL={until}",
                f"SUMMARY:{_esc(summary)}",
                f"LOCATION:{_esc(m.where())}",
                f"DESCRIPTION:{_esc(desc)}",
                "END:VEVENT",
            ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
