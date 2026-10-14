"""Alternate data source: the community umd.io API (api.umd.io/v1).

umd.io mirrors Testudo; it is handy when Testudo's markup changes, but it
can lag behind on seat counts.
"""

from __future__ import annotations

from umdsched.models import ONLINE, Course, Meeting, Section
from umdsched.timeutil import parse_days, parse_time

BASE_URL = "https://api.umd.io/v1"


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _time(v) -> int | None:
    try:
        return parse_time(v, assume_pm_for_small=False) if v else None
    except ValueError:
        return None


def parse_meeting(m: dict) -> Meeting:
    building = (m.get("building") or "").strip() or None
    room = (m.get("room") or "").strip() or None
    if room and room.upper() == ONLINE:
        building, room = ONLINE, None
    try:
        days = parse_days(m.get("days") or "")
    except ValueError:
        days = ()
    start, end = _time(m.get("start_time")), _time(m.get("end_time"))
    if not days or start is None or end is None:
        days, start, end = (), None, None
    return Meeting(days, start, end, building, room, m.get("classtype") or "Lecture")


def parse_section(course_id: str, s: dict) -> Section:
    sid = str(s.get("number") or s.get("section_id", "")).split("-")[-1]
    meetings = [parse_meeting(m) for m in s.get("meetings") or []]
    online = meetings and all(m.online or not m.timed for m in meetings) and any(m.online for m in meetings)
    return Section(
        course_id=course_id,
        section_id=sid,
        instructors=[i for i in s.get("instructors") or [] if i and i.upper() != "TBA"],
        meetings=meetings,
        seats_total=_int(s.get("seats")),
        seats_open=_int(s.get("open_seats")),
        waitlist=_int(s.get("waitlist")),
        delivery="online" if online else "f2f",
    )


def fetch_course(course_id: str, term: str, session=None) -> Course | None:
    import requests

    sess = session or requests.Session()
    cid = course_id.upper()
    resp = sess.get(f"{BASE_URL}/courses/{cid}", params={"semester": term}, timeout=30)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    data = resp.json()
    info = data[0] if isinstance(data, list) and data else data
    if not info:
        return None
    resp = sess.get(f"{BASE_URL}/courses/{cid}/sections", params={"semester": term}, timeout=30)
    resp.raise_for_status()
    return Course(
        course_id=cid,
        title=info.get("name") or "",
        credits=_int(info.get("credits")) or 3,
        sections=[parse_section(cid, s) for s in resp.json()],
    )
