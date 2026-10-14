"""Scraper for the UMD Schedule of Classes (app.testudo.umd.edu/soc).

Testudo has no public API, so this parses its HTML. The search page lists
each course in a <div class="course" id="CMSC216"> and, when sections are
not rendered inline, they are fetched from /soc/<term>/sections?courseIds=..
which returns the same section markup.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

from umdsched.models import ONLINE, Course, Meeting, Section
from umdsched.timeutil import parse_days, parse_time

BASE_URL = "https://app.testudo.umd.edu/soc"
USER_AGENT = "umd-sched/0.1 (student schedule planner; +https://github.com/jemminiz/umd-classes)"


def search_params(course_id: str, term: str) -> dict[str, str]:
    """Query string for Testudo's search form with every filter left open."""
    params = {
        "courseId": course_id,
        "sectionId": "",
        "termId": term,
        "_openSectionsOnly": "on",
        "creditCompare": "",
        "credits": "",
        "courseLevelFilter": "ALL",
        "instructor": "",
        "_facetoface": "on",
        "_blended": "on",
        "_online": "on",
        "courseStartCompare": "",
        "courseStartHour": "",
        "courseStartMin": "",
        "courseStartAM": "",
        "courseEndHour": "",
        "courseEndMin": "",
        "courseEndAM": "",
        "teachingCenter": "ALL",
    }
    for i in range(1, 6):
        params[f"_classDay{i}"] = "on"
    return params


def _soup(html: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:  # lxml not installed
        return BeautifulSoup(html, "html.parser")


def _text(node: Tag | None, selector: str) -> str:
    if node is None:
        return ""
    found = node.select_one(selector)
    return " ".join(found.get_text(" ", strip=True).split()) if found else ""


def _int(text: str) -> int | None:
    m = re.search(r"-?\d+", text or "")
    return int(m.group(0)) if m else None


def _time(text: str) -> int | None:
    text = text.strip()
    if not text:
        return None
    try:
        return parse_time(text, assume_pm_for_small=False)
    except ValueError:
        return None


def parse_meeting(row: Tag) -> Meeting | None:
    days_text = _text(row, ".section-days")
    start = _time(_text(row, ".class-start-time"))
    end = _time(_text(row, ".class-end-time"))
    building = _text(row, ".building-code") or None
    room = _text(row, ".class-room") or None
    kind = _text(row, ".class-type") or "Lecture"
    row_text = row.get_text(" ", strip=True).upper()

    if room and room.upper() == ONLINE and not building:
        building, room = ONLINE, None
    elif not building and ("ONLINE" in row_text or "ELMS" in row_text):
        building = ONLINE

    try:
        days = parse_days(days_text) if days_text and "TBA" not in days_text.upper() else ()
    except ValueError:
        days = ()
    if not days_text and start is None and building is None and room is None:
        return None  # a layout row, not a meeting
    if not days or start is None or end is None:
        days, start, end = (), None, None
    return Meeting(days=days, start=start, end=end, building=building, room=room, kind=kind)


def parse_section(node: Tag, course_id: str) -> Section | None:
    section_id = _text(node, ".section-id")
    if not section_id:
        return None
    instructors = []
    for inst in node.select(".section-instructor"):
        name = " ".join(inst.get_text(" ", strip=True).split())
        if name and name.lower() not in ("instructor: tba", "tba") and name not in instructors:
            instructors.append(name)

    waitlists = node.select(".waitlist-count")
    classes = " ".join(node.get("class", []))
    delivery = "f2f"
    if "delivery-blended" in classes:
        delivery = "blended"
    elif "delivery-online" in classes:
        delivery = "online"

    container = node.select_one(".class-days-container") or node
    # Innermost .row elements only, so nested layout rows aren't read twice.
    rows = [r for r in container.select(".row") if not r.select_one(".row")] or [container]
    meetings = [m for m in (parse_meeting(r) for r in rows) if m is not None]
    if delivery == "f2f" and meetings and all(m.online or not m.timed for m in meetings):
        if any(m.online for m in meetings):
            delivery = "online"

    return Section(
        course_id=course_id,
        section_id=section_id,
        instructors=instructors,
        meetings=meetings,
        seats_total=_int(_text(node, ".total-seats-count")),
        seats_open=_int(_text(node, ".open-seats-count")),
        waitlist=_int(waitlists[0].get_text()) if waitlists else None,
        holdfile=_int(waitlists[1].get_text()) if len(waitlists) > 1 else None,
        delivery=delivery,
    )


def parse_sections(html_or_node, course_id: str) -> list[Section]:
    root = _soup(html_or_node) if isinstance(html_or_node, str) else html_or_node
    sections = []
    for node in root.select("div.section"):
        sec = parse_section(node, course_id)
        if sec:
            sections.append(sec)
    return sections


def parse_sections_page(html: str) -> dict[str, list[Section]]:
    """Parse /soc/<term>/sections?courseIds=A,B which groups sections by course."""
    soup = _soup(html)
    out: dict[str, list[Section]] = {}
    groups = soup.select(".course-sections[id]")
    for group in groups:
        cid = group["id"].strip().upper()
        out[cid] = parse_sections(group, cid)
    return out


def parse_courses(html: str) -> list[Course]:
    """Parse a Testudo search or department page into courses (with any inline sections)."""
    soup = _soup(html)
    courses = []
    for node in soup.select("div.course[id]"):
        cid = (_text(node, ".course-id") or node["id"]).strip().upper()
        min_credits = _int(_text(node, ".course-min-credits"))
        max_credits = _int(_text(node, ".course-max-credits"))
        courses.append(
            Course(
                course_id=cid,
                title=_text(node, ".course-title"),
                credits=min_credits if min_credits is not None else 3,
                credits_max=max_credits,
                sections=parse_sections(node, cid),
            )
        )
    return courses


def fetch_course(course_id: str, term: str, session=None) -> Course | None:
    """Fetch one course (with sections) from Testudo, or None if it isn't offered."""
    import requests

    sess = session or requests.Session()
    sess.headers.setdefault("User-Agent", USER_AGENT)
    resp = sess.get(f"{BASE_URL}/search", params=search_params(course_id, term), timeout=30)
    resp.raise_for_status()
    match = next((c for c in parse_courses(resp.text) if c.course_id == course_id.upper()), None)
    if match is None:
        return None
    if not match.sections:
        resp = sess.get(f"{BASE_URL}/{term}/sections", params={"courseIds": match.course_id}, timeout=30)
        resp.raise_for_status()
        by_course = parse_sections_page(resp.text)
        match.sections = by_course.get(match.course_id) or parse_sections(resp.text, match.course_id)
    return match
