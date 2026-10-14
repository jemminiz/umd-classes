"""Course specs: a compact way to say which sections and professors you want.

    CMSC216                 any section
    CMSC216:0101,0102       only these sections
    CMSC216:01*             sections matching a wildcard
    CMSC216:~0201           any section except 0201 (! and - also work)
    CMSC216@Herman          only sections taught by someone matching "Herman"
    CMSC216@~Herman         avoid sections taught by "Herman"
    CMSC216:01*@Herman      combine section and professor filters
    "ENGL101|ENGL102"       exactly one of these courses (/ also works)
    STAT400?                optional: include it only if it fits

Professor matching is a case-insensitive substring match against each of
the section's instructors, so "herman" matches "Larry Herman".
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field

from umdsched.models import Section

COURSE_RE = re.compile(r"^[A-Za-z]{4}\d{3}[A-Za-z]?$")
_NEGATE = ("~", "!", "-")


@dataclass
class CourseChoice:
    course_id: str
    include_sections: list[str] = field(default_factory=list)
    exclude_sections: list[str] = field(default_factory=list)
    include_profs: list[str] = field(default_factory=list)
    exclude_profs: list[str] = field(default_factory=list)

    def section_allowed(self, section: Section) -> bool:
        sid = section.section_id
        if self.include_sections and not any(fnmatch.fnmatch(sid, p) for p in self.include_sections):
            return False
        if any(fnmatch.fnmatch(sid, p) for p in self.exclude_sections):
            return False
        names = [i.lower() for i in section.instructors]
        if self.include_profs and not any(p.lower() in n for p in self.include_profs for n in names):
            return False
        if any(p.lower() in n for p in self.exclude_profs for n in names):
            return False
        return True

    def describe(self) -> str:
        s = self.course_id
        sec = self.include_sections + [f"~{x}" for x in self.exclude_sections]
        if sec:
            s += ":" + ",".join(sec)
        profs = self.include_profs + [f"~{x}" for x in self.exclude_profs]
        if profs:
            s += "@" + ",".join(profs)
        return s


@dataclass
class CourseGroup:
    """One slot in the schedule: exactly one alternative (or none, if optional)."""

    alternatives: list[CourseChoice]
    optional: bool = False

    def describe(self) -> str:
        s = "|".join(a.describe() for a in self.alternatives)
        return s + ("?" if self.optional else "")


def _split_negations(items: list[str]) -> tuple[list[str], list[str]]:
    inc, exc = [], []
    for item in items:
        item = item.strip()
        if not item:
            continue
        if item[0] in _NEGATE:
            exc.append(item[1:].strip())
        else:
            inc.append(item)
    return inc, exc


def parse_choice(text: str) -> CourseChoice:
    text = text.strip()
    profs_part = ""
    if "@" in text:
        text, profs_part = text.split("@", 1)
    sections_part = ""
    if ":" in text:
        text, sections_part = text.split(":", 1)
    cid = text.strip().upper()
    if not COURSE_RE.match(cid):
        raise ValueError(f"{cid!r} doesn't look like a course id (e.g. CMSC216, MATH140H)")
    sec_inc, sec_exc = _split_negations(sections_part.split(","))
    sec_inc = [s.zfill(4) if s.isdigit() else s for s in sec_inc]
    sec_exc = [s.zfill(4) if s.isdigit() else s for s in sec_exc]
    prof_inc, prof_exc = _split_negations(profs_part.split(","))
    return CourseChoice(
        course_id=cid,
        include_sections=sec_inc,
        exclude_sections=sec_exc,
        include_profs=prof_inc,
        exclude_profs=prof_exc,
    )


def parse_spec(text: str, *, optional: bool = False) -> CourseGroup:
    text = text.strip()
    if text.endswith("?"):
        optional, text = True, text[:-1]
    alts = [a for a in re.split(r"[|/]", text) if a.strip()]
    if not alts:
        raise ValueError("empty course spec")
    return CourseGroup([parse_choice(a) for a in alts], optional=optional)
