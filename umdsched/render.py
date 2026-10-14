"""Text and JSON output for schedules and section listings."""

from __future__ import annotations

import shutil

from umdsched.models import Course
from umdsched.scheduler import Schedule
from umdsched.timeutil import DAY_CODES, format_days, format_duration, format_time


def _trunc(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def schedule_summary(s: Schedule) -> str:
    days = format_days(s.campus_days) or "none"
    span = f"{format_time(s.first_start)}–{format_time(s.last_end)}" if s.first_start is not None else "no timed classes"
    parts = [
        f"{len(s.campus_days)} campus days ({days})",
        span,
        f"{format_duration(s.idle_minutes)} idle/wk",
        f"~{s.walk_minutes:.0f} min walking/wk",
        f"{s.credits} cr",
    ]
    return " · ".join(parts)


def render_schedule(i: int, s: Schedule, *, ratings: dict | None = None, grid: bool = True) -> str:
    lines = [f"#{i}  cost {s.cost:g}  ·  {schedule_summary(s)}"]
    costs = ", ".join(f"{k} {v:+g}" for k, v in sorted(s.breakdown.items(), key=lambda kv: -abs(kv[1])))
    if costs:
        lines.append(f"    why: {costs}")
    for o in s.options:
        secs = "/".join(o.section_ids[:4]) + ("/…" if len(o.section_ids) > 4 else "")
        prof = ", ".join(o.instructors) or "TBA"
        if ratings is not None:
            rs = [ratings.get(n) for n in o.instructors]
            rs = [r for r in rs if r is not None]
            prof += f" (★{sum(rs) / len(rs):.1f})" if rs else " (★?)"
        lines.append(f"    {o.course_id:<9}{_trunc(secs, 14):<15}{_trunc(prof, 30):<31}{o.best.seats_str()}")
        for m in o.meetings:
            kind = "" if m.kind == "Lecture" else f" [{m.kind}]"
            lines.append(f"        {m.when():<26}{m.where()}{kind}")
    if s.variants:
        lines.append(f"    same times, other sections ({s.variant_count} combination{'s' if s.variant_count != 1 else ''}):")
        for v in s.variants[:4]:
            lines.append(f"      - {describe_swap(s, v)}  (cost {v.cost:g})")
        if len(s.variants) > 4:
            lines.append(f"      - … and {len(s.variants) - 4} more single swaps")
    for t in s.tight:
        flag = "can't make it" if t.slack < 0 else "tight"
        lines.append(f"    ! {flag}: {t.describe()}")
    if s.no_lunch_days:
        lines.append(f"    ! no lunch break on {format_days(s.no_lunch_days)}")
    if grid:
        lines.append(render_grid(s))
    return "\n".join(lines)


def describe_swap(base: Schedule, other: Schedule) -> str:
    """Summarize how `other` differs from `base`, e.g. "CMSC216 0201 (Vale) for 0101"."""
    base_by_group = {o.group_index: o for o in base.options}
    parts = []
    for o in other.options:
        b = base_by_group.get(o.group_index)
        if b is not None and b.section_ids == o.section_ids and b.course_id == o.course_id:
            continue
        prof = ", ".join(i.split()[-1] for i in o.instructors) or "TBA"
        was = "" if b is None else (f" for {b.section_ids[0]}" if b.course_id == o.course_id else f" for {b.course_id}")
        parts.append(f"{o.course_id} {o.section_ids[0]} ({prof}){was}")
    return "; ".join(parts) or "equivalent"


def render_grid(s: Schedule, *, step: int = 30) -> str:
    """A week-at-a-glance grid with one row per half hour."""
    days = [d for d in range(7) if d < 5 or d in s.class_days]
    timed = [(m, o.course_id) for o in s.options for m in o.timed]
    if not timed:
        return "    (no timed meetings)"
    lo = min(m.start for m, _ in timed) // step * step
    hi = -(-max(m.end for m, _ in timed) // step) * step
    width = max(8, min(14, (shutil.get_terminal_size((100, 20)).columns - 12) // len(days) - 1))
    header = "          " + "".join(f"{DAY_CODES[d]:<{width + 1}}" for d in days)
    out = ["", header]
    for t in range(lo, hi, step):
        cells = []
        for d in days:
            cell = ""
            for m, cid in timed:
                if d in m.days and m.start < t + step and t < m.end:
                    if m.start >= t:  # first slot of this meeting
                        cell = cid
                    elif m.start >= t - step:
                        cell = m.where() if m.building else "▒" * (width - 2)
                    else:
                        cell = "▒" * (width - 2)
                    break
            cells.append(f"{_trunc(cell, width):<{width}}")
        out.append(f"  {format_time(t):>7} " + "|".join(cells))
    return "\n".join("    " + line if line else line for line in out)


def render_course(course: Course, *, ratings: dict | None = None) -> str:
    lines = [f"{course.course_id}  {course.title}  ({course.credits} credits, {len(course.sections)} sections)"]
    for sec in course.sections:
        prof = sec.instructor_str()
        if ratings is not None:
            rs = [r for r in (ratings.get(n) for n in sec.instructors) if r is not None]
            prof += f" (★{sum(rs) / len(rs):.1f})" if rs else " (★?)"
        lines.append(f"  {sec.section_id:<6}{_trunc(prof, 34):<35}{sec.seats_str():<26}{sec.delivery}")
        for m in sec.meetings:
            kind = "" if m.kind == "Lecture" else f" [{m.kind}]"
            lines.append(f"          {m.when():<26}{m.where()}{kind}")
    return "\n".join(lines)


def schedule_to_dict(s: Schedule) -> dict:
    return {
        "cost": s.cost,
        "breakdown": s.breakdown,
        "credits": s.credits,
        "campus_days": format_days(s.campus_days),
        "idle_minutes": round(s.idle_minutes),
        "walk_minutes": round(s.walk_minutes, 1),
        "first_start": format_time(s.first_start) if s.first_start is not None else None,
        "last_end": format_time(s.last_end) if s.last_end is not None else None,
        "sections": [
            {
                "course": o.course_id,
                "title": o.course.title,
                "sections": o.section_ids,
                "instructors": o.instructors,
                "open": o.is_open,
                "seats": o.best.seats_str(),
                "meetings": [m.to_dict() for m in o.meetings],
            }
            for o in s.options
        ],
        "variants": [
            [{"course": o.course_id, "sections": o.section_ids, "instructors": o.instructors} for o in v.options]
            for v in s.variants
        ],
        "warnings": [t.describe() for t in s.tight]
        + ([f"no lunch break on {format_days(s.no_lunch_days)}"] if s.no_lunch_days else []),
    }
