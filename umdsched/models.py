"""Data model for courses, sections and meetings."""

from __future__ import annotations

from dataclasses import dataclass, field

from umdsched.timeutil import format_days, format_time

ONLINE = "ONLINE"


@dataclass(frozen=True)
class Meeting:
    days: tuple[int, ...]
    start: int | None  # minutes after midnight
    end: int | None
    building: str | None = None  # building code, "ONLINE", or None if TBA
    room: str | None = None
    kind: str = "Lecture"  # Lecture, Discussion, Lab, ...

    @property
    def timed(self) -> bool:
        return bool(self.days) and self.start is not None and self.end is not None

    @property
    def online(self) -> bool:
        return (self.building or "").upper() == ONLINE

    @property
    def in_person(self) -> bool:
        return self.timed and not self.online

    def where(self) -> str:
        if self.online:
            return "online"
        if not self.building:
            return "TBA"
        return f"{self.building} {self.room}" if self.room else self.building

    def when(self) -> str:
        if not self.timed:
            return "async/TBA"
        return f"{format_days(self.days)} {format_time(self.start)}-{format_time(self.end)}"

    def to_dict(self) -> dict:
        return {
            "days": format_days(self.days),
            "start": format_time(self.start) if self.start is not None else None,
            "end": format_time(self.end) if self.end is not None else None,
            "building": self.building,
            "room": self.room,
            "kind": self.kind,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Meeting":
        from umdsched.timeutil import parse_days, parse_time

        def t(v):
            return parse_time(v, assume_pm_for_small=False) if v else None

        return cls(
            days=parse_days(d.get("days") or ""),
            start=t(d.get("start")),
            end=t(d.get("end")),
            building=d.get("building"),
            room=d.get("room"),
            kind=d.get("kind") or "Lecture",
        )


@dataclass
class Section:
    course_id: str
    section_id: str
    instructors: list[str] = field(default_factory=list)
    meetings: list[Meeting] = field(default_factory=list)
    seats_total: int | None = None
    seats_open: int | None = None
    waitlist: int | None = None
    holdfile: int | None = None
    delivery: str = "f2f"  # f2f, blended, online

    @property
    def key(self) -> str:
        return f"{self.course_id}-{self.section_id}"

    @property
    def is_open(self) -> bool:
        return self.seats_open is None or self.seats_open > 0

    def instructor_str(self) -> str:
        return ", ".join(self.instructors) if self.instructors else "Instructor: TBA"

    def seats_str(self) -> str:
        if self.seats_open is None:
            return "seats ?"
        s = f"{self.seats_open}/{self.seats_total} open" if self.seats_total is not None else f"{self.seats_open} open"
        if self.waitlist:
            s += f", waitlist {self.waitlist}"
        return s

    def to_dict(self) -> dict:
        return {
            "section_id": self.section_id,
            "instructors": list(self.instructors),
            "meetings": [m.to_dict() for m in self.meetings],
            "seats_total": self.seats_total,
            "seats_open": self.seats_open,
            "waitlist": self.waitlist,
            "holdfile": self.holdfile,
            "delivery": self.delivery,
        }

    @classmethod
    def from_dict(cls, course_id: str, d: dict) -> "Section":
        return cls(
            course_id=course_id,
            section_id=d["section_id"],
            instructors=list(d.get("instructors") or []),
            meetings=[Meeting.from_dict(m) for m in d.get("meetings") or []],
            seats_total=d.get("seats_total"),
            seats_open=d.get("seats_open"),
            waitlist=d.get("waitlist"),
            holdfile=d.get("holdfile"),
            delivery=d.get("delivery") or "f2f",
        )


@dataclass
class Course:
    course_id: str
    title: str = ""
    credits: int = 3
    credits_max: int | None = None
    sections: list[Section] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "course_id": self.course_id,
            "title": self.title,
            "credits": self.credits,
            "credits_max": self.credits_max,
            "sections": [s.to_dict() for s in self.sections],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Course":
        cid = d["course_id"].upper()
        return cls(
            course_id=cid,
            title=d.get("title") or "",
            credits=int(d.get("credits") or 0),
            credits_max=d.get("credits_max"),
            sections=[Section.from_dict(cid, s) for s in d.get("sections") or []],
        )
