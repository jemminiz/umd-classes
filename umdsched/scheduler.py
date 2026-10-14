"""Generate and rank conflict-free schedules.

Sections of a course that meet at identical times and places with the same
instructor are collapsed into one "option" (you'd pick whichever has
seats). Options in a course group that put you in the same buildings at the
same times are interchangeable as far as time and walking go, so only the
cheapest is searched and the rest are shown as alternates. For every pair of options in different course groups we precompute
whether they can coexist -- no overlap, enough time to walk between
buildings -- as bitmasks, then backtrack over groups with forward checking.

Complete schedules are scored and the best kept. Branch-and-bound keeps big
searches fast: parts of the cost only grow as classes are added (campus
days, early/late penalties, per-section costs), so once the shortlist is
full, any partial schedule whose lower bound is already worse is skipped.
"""

from __future__ import annotations

import heapq
import itertools
from collections import Counter
from dataclasses import dataclass, field

from umdsched.buildings import WalkModel
from umdsched.models import Course, Meeting, Section
from umdsched.prefs import Preferences
from umdsched.specs import CourseChoice, CourseGroup
from umdsched.timeutil import DAY_CODES, format_time


@dataclass
class Option:
    course: Course
    sections: list[Section]  # equivalent sections, best (most open seats) first
    group_index: int = -1
    # Filled in by Scheduler.prepare():
    static: dict[str, float] = field(default_factory=dict)  # per-section costs (seats, profs, ...)
    static_total: float = 0.0
    day_mask: int = 0  # in-person days as a bitmask
    # Per weekday: (start, end, building-if-in-person-else-None, meeting, course id)
    by_day: list[list[tuple]] = field(default_factory=list)
    sig: tuple = ()  # this option's timetable footprint
    alternates: list["Option"] = field(default_factory=list)  # same footprint, costlier

    @property
    def course_id(self) -> str:
        return self.course.course_id

    @property
    def meetings(self) -> list[Meeting]:
        return self.sections[0].meetings

    @property
    def instructors(self) -> list[str]:
        return self.sections[0].instructors

    @property
    def section_ids(self) -> list[str]:
        return [s.section_id for s in self.sections]

    @property
    def is_open(self) -> bool:
        return any(s.is_open for s in self.sections)

    @property
    def best(self) -> Section:
        return self.sections[0]

    @property
    def timed(self) -> list[Meeting]:
        return [m for m in self.meetings if m.timed]


@dataclass
class Transition:
    day: int
    frm: Meeting
    to: Meeting
    frm_course: str
    to_course: str
    gap: int
    walk: float

    @property
    def slack(self) -> float:
        return self.gap - self.walk

    def describe(self) -> str:
        return (
            f"{DAY_CODES[self.day]} {format_time(self.frm.end)} {self.frm_course} ({self.frm.where()}) -> "
            f"{format_time(self.to.start)} {self.to_course} ({self.to.where()}): "
            f"{self.walk:.0f} min walk in a {self.gap} min gap"
        )


@dataclass
class Schedule:
    options: list[Option]
    cost: float
    breakdown: dict[str, float]
    campus_days: list[int]
    class_days: list[int]
    idle_minutes: float
    walk_minutes: float
    first_start: int | None
    last_end: int | None
    credits: int
    tight: list[Transition] = field(default_factory=list)
    no_lunch_days: list[int] = field(default_factory=list)
    optional_included: int = 0
    # The same timetable with one section swapped for an interchangeable one
    # (same times and buildings, e.g. a different professor), cheapest first.
    variants: list["Schedule"] = field(default_factory=list)
    variant_count: int = 0  # every combination of swaps, not just single ones


@dataclass
class SearchResult:
    schedules: list[Schedule]
    total_found: int  # valid distinct timetables
    truncated: bool  # hit max_schedules, so total_found is a lower bound
    exact: bool  # False when total_found may include a few that --require-lunch would reject
    filter_report: dict[str, Counter]  # course id -> reason -> sections removed
    options_per_group: list[int]
    diagnosis: list[str]


def _overlaps(m: Meeting, days, start: int, end: int) -> bool:
    return bool(set(m.days) & set(days)) and m.start < end and start < m.end


def section_filter_reason(section: Section, choice: CourseChoice, prefs: Preferences) -> str | None:
    """Why a section is excluded by the hard constraints, or None if it's allowed."""
    if not choice.section_allowed(section):
        return "your section/professor filter"
    names = [i.lower() for i in section.instructors]
    if any(p in n for p in prefs.avoid_profs for n in names):
        return "--avoid-prof"
    if section.delivery not in prefs.delivery:
        return f"delivery mode ({section.delivery})"
    if prefs.open_only and not section.is_open:
        return "--open-only (no open seats)"
    if prefs.max_waitlist is not None and not section.is_open and (section.waitlist or 0) > prefs.max_waitlist:
        return "--max-waitlist"
    for m in section.meetings:
        if not m.timed:
            continue
        if prefs.earliest is not None and m.start < prefs.earliest:
            return "--earliest"
        if prefs.latest is not None and m.end > prefs.latest:
            return "--latest"
        if set(m.days) & prefs.days_off:
            return "--days-off"
        for days, start, end in prefs.blocks:
            if _overlaps(m, days, start, end):
                return "--block"
    return None


def build_options(course: Course, choice: CourseChoice, prefs: Preferences) -> tuple[list[Option], Counter]:
    removed: Counter = Counter()
    by_key: dict[tuple, list[Section]] = {}
    for sec in course.sections:
        reason = section_filter_reason(sec, choice, prefs)
        if reason:
            removed[reason] += 1
            continue
        key = (
            tuple(sorted((m.days, m.start, m.end, m.building or "", m.room or "") for m in sec.meetings)),
            tuple(sorted(sec.instructors)),
        )
        by_key.setdefault(key, []).append(sec)
    options = []
    for secs in by_key.values():
        secs.sort(key=lambda s: (not s.is_open, -(s.seats_open or 0), s.waitlist or 0, s.section_id))
        options.append(Option(course=course, sections=secs))
    options.sort(key=lambda o: o.section_ids[0])
    return options, removed


class Scheduler:
    def __init__(
        self,
        courses: dict[str, Course],
        groups: list[CourseGroup],
        prefs: Preferences,
        walk: WalkModel | None = None,
        ratings: dict[str, float | None] | None = None,
    ):
        self.courses = courses
        self.groups = groups
        self.prefs = prefs
        self.walk = walk or WalkModel(prefs.walk_speed, prefs.path_factor, unknown_min=prefs.unknown_walk)
        self.ratings = ratings or {}
        self._walk_cache: dict[tuple, float] = {}

    # ----- helpers -------------------------------------------------------

    def walk_minutes(self, a: Meeting, b: Meeting) -> float:
        if not (a.in_person and b.in_person):
            return 0.0
        return self._walk(a.building, b.building)

    def _walk(self, a: str | None, b: str | None) -> float:
        """Walk between two in-person building codes; None means not in person."""
        if a is None or b is None:
            return 0.0
        key = (a, b)
        cached = self._walk_cache.get(key)
        if cached is None:
            cached = self._walk_cache[key] = self.walk.minutes(a, b)
        return cached

    def _pair_ok(self, a: Meeting, b: Meeting) -> bool:
        if not (a.timed and b.timed) or not set(a.days) & set(b.days):
            return True
        if a.start < b.end and b.start < a.end:
            return False
        first, second = (a, b) if a.start <= b.start else (b, a)
        gap = second.start - first.end
        if gap < self.prefs.min_gap:
            return False
        if not self.prefs.allow_unwalkable and self.walk_minutes(first, second) > gap:
            return False
        return True

    def compatible(self, o1: Option, o2: Option) -> bool:
        if o1.course_id == o2.course_id:
            return False
        return all(self._pair_ok(a, b) for a in o1.timed for b in o2.timed)

    # ----- search --------------------------------------------------------

    def _constants(self) -> None:
        prefs, w = self.prefs, self.prefs.weights
        self._k = (
            w["day"], w["early_hour"] / 60, w["late_hour"] / 60, w["idle_hour"] / 60, w["no_lunch"],
            w["tight"], w["walk_minute"], prefs.preferred_start, prefs.preferred_end,
            prefs.lunch_window[0], prefs.lunch_window[1], prefs.lunch_minutes, prefs.min_buffer,
            prefs.lunch, prefs.require_lunch,
        )

    def search(self, *, keep: int = 10, max_schedules: int = 300_000) -> SearchResult:
        """Find the `keep` cheapest distinct timetables."""
        prefs = self.prefs
        self._constants()
        group_options: list[list[Option]] = []
        filter_report: dict[str, Counter] = {}
        for gi, group in enumerate(self.groups):
            opts: list[Option] = []
            for choice in group.alternatives:
                course = self.courses[choice.course_id]
                o, removed = build_options(course, choice, prefs)
                filter_report[choice.course_id] = removed
                opts.extend(o)
            for o in opts:
                o.group_index = gi
                self.prepare(o)
            # Most promising options first, so the shortlist fills with good schedules early.
            group_options.append(sorted(self._collapse(opts), key=self._option_heuristic))

        diagnosis = []
        for group, opts in zip(self.groups, group_options):
            if not opts and not group.optional:
                ids = ", ".join(a.course_id for a in group.alternatives)
                diagnosis.append(f"{ids}: no sections left after your filters")
        if diagnosis:
            return SearchResult([], 0, False, True, filter_report, [len(o) for o in group_options], diagnosis)

        # Search groups with the fewest options first; it prunes earliest.
        order = sorted(range(len(group_options)), key=lambda i: (len(group_options[i]), i))
        ordered = [group_options[i] for i in order]
        optional = [self.groups[i].optional for i in order]
        n = len(ordered)

        # compat[g][k][j] = bitmask of options in group j (> g) compatible with option k of group g.
        compat: list[list[list[int]]] = []
        for g in range(n):
            rows = []
            for opt in ordered[g]:
                masks = [0] * n
                for j in range(g + 1, n):
                    mask = 0
                    for k2, other in enumerate(ordered[j]):
                        if self.compatible(opt, other):
                            mask |= 1 << k2
                    masks[j] = mask
                rows.append(masks)
            compat.append(rows)

        credit_min = [
            min(o.course.credits for o in opts) if opts and not opt_flag else 0
            for opts, opt_flag in zip(ordered, optional)
        ]
        remaining_credits = [sum(credit_min[g:]) for g in range(n + 1)]
        static_min = [
            min([o.static_total for o in opts] + ([0.0] if opt_flag else []))
            for opts, opt_flag in zip(ordered, optional)
        ]
        remaining_static = [sum(static_min[g:]) for g in range(n + 1)]

        fill = self._fill_table(ordered)

        w = prefs.weights
        monotone_keys = ("day", "idle_hour", "early_hour", "late_hour", "no_lunch", "tight", "walk_minute")
        can_prune = all(w[k] >= 0 for k in monotone_keys)
        w_day, w_early, w_late = w["day"], w["early_hour"] / 60, w["late_hour"] / 60
        pstart, pend = prefs.preferred_start, prefs.preferred_end
        simple_leaves = prefs.max_days is None and prefs.min_credits is None and prefs.max_credits is None

        heap: list[tuple[float, int, tuple[Option, ...]]] = []
        counter = itertools.count()
        scored = 0
        found = 0
        truncated = False
        pruned = False
        chosen: list[Option] = []

        def lower_bound(g: int, day_mask: int, first: list, last: list, static: float) -> float:
            lb = bin(day_mask).count("1") * w_day + static + remaining_static[g]
            for d in range(7):
                if first[d] is not None:
                    if first[d] < pstart:
                        lb += (pstart - first[d]) * w_early
                    if last[d] > pend:
                        lb += (last[d] - pend) * w_late
            return lb

        def children(g: int, allowed: list[int], day_mask: int):
            """Yield (option, allowed masks after choosing it, new day mask) for group g."""
            mask, k = allowed[g], 0
            while mask:
                if mask & 1:
                    opt = ordered[g][k]
                    new_days = day_mask | opt.day_mask
                    if prefs.max_days is None or bin(new_days).count("1") <= prefs.max_days:
                        row = compat[g][k]
                        new_allowed = allowed[:]
                        for j in range(g + 1, n):
                            new_allowed[j] &= row[j]
                            if not new_allowed[j] and not optional[j]:
                                break
                        else:
                            yield opt, new_allowed, new_days
                mask >>= 1
                k += 1

        def count(g: int, allowed: list[int], credits: int, day_mask: int) -> int:
            """Count valid completions without scoring them (for pruned subtrees)."""
            if prefs.max_credits is not None and credits + remaining_credits[g] > prefs.max_credits:
                return 0
            if g == n:
                return 0 if prefs.min_credits is not None and credits < prefs.min_credits else 1
            if g == n - 1 and simple_leaves:
                return bin(allowed[g]).count("1") + (1 if optional[g] else 0)
            total = sum(count(g + 1, a, credits + o.course.credits, dm) for o, a, dm in children(g, allowed, day_mask))
            if optional[g]:
                total += count(g + 1, allowed, credits, day_mask)
            return total

        def dfs(g: int, allowed: list[int], credits: int, day_mask: int, first: list, last: list, static: float) -> None:
            nonlocal scored, found, truncated, pruned
            if truncated:
                return
            if prefs.max_credits is not None and credits + remaining_credits[g] > prefs.max_credits:
                return
            if can_prune and len(heap) >= keep and lower_bound(g, day_mask, first, last, static) > -heap[0][0]:
                pruned = True
                found += count(g, allowed, credits, day_mask)
                return
            if g == n - 1:
                # Last group: score children directly, reusing the parent's per-day costs.
                bound, children_costs = self._expand_last(
                    chosen, allowed[g], optional[g], fill[g], ordered[g], static
                )
                if bound is None or (can_prune and len(heap) >= keep and bound + static + remaining_static[g] > -heap[0][0]):
                    pruned = True
                    if bound is not None or not prefs.require_lunch:
                        found += count(g, allowed, credits, day_mask)
                    return
                for cost, opt in children_costs:
                    if cost is None:
                        continue
                    c = credits + (opt.course.credits if opt else 0)
                    if prefs.min_credits is not None and c < prefs.min_credits:
                        continue
                    if prefs.max_credits is not None and c > prefs.max_credits:
                        continue
                    if opt is not None and prefs.max_days is not None and bin(day_mask | opt.day_mask).count("1") > prefs.max_days:
                        continue
                    scored += 1
                    found += 1
                    if len(heap) < keep or -cost > heap[0][0]:
                        item = (-cost, next(counter), tuple(chosen) + ((opt,) if opt else ()))
                        if len(heap) < keep:
                            heapq.heappush(heap, item)
                        else:
                            heapq.heapreplace(heap, item)
                if scored >= max_schedules:
                    truncated = True
                return
            for opt, new_allowed, new_days in children(g, allowed, day_mask):
                new_first, new_last = first[:], last[:]
                for d, items in enumerate(opt.by_day):
                    for start, end, *_ in items:
                        if new_first[d] is None or start < new_first[d]:
                            new_first[d] = start
                        if new_last[d] is None or end > new_last[d]:
                            new_last[d] = end
                chosen.append(opt)
                dfs(g + 1, new_allowed, credits + opt.course.credits, new_days, new_first, new_last, static + opt.static_total)
                chosen.pop()
                if truncated:
                    return
            if optional[g]:
                dfs(g + 1, allowed, credits, day_mask, first, last, static)

        dfs(0, [(1 << len(opts)) - 1 for opts in ordered], 0, 0, [None] * 7, [None] * 7, 0.0)

        schedules = []
        for _, _, opts in sorted(heap, key=lambda t: (-t[0], t[1])):
            sched = self._score(list(opts), detail=True)
            self._add_variants(sched, list(opts))
            schedules.append(sched)
        if not schedules:
            diagnosis = self.diagnose(group_options)
        return SearchResult(
            schedules, found, truncated, not (pruned and prefs.require_lunch),
            filter_report, [len(o) for o in group_options], diagnosis,
        )

    def _fill_table(self, ordered: list[list[Option]]) -> list[list[float]]:
        """fill[g][d]: most minutes of weekday d that groups g.. could still occupy.

        Adding a class to a gap removes at most its length plus the walks to
        and from it, which bounds how much idle time can shrink.
        """
        buildings = {it[2] for opts in ordered for o in opts for items in o.by_day for it in items if it[2]}
        max_walk = max([self._walk(a, b) for a in buildings for b in buildings] + [0.0])
        n = len(ordered)
        fill = [[0.0] * 7 for _ in range(n + 1)]
        for g in range(n - 1, -1, -1):
            for d in range(7):
                most = max([sum(it[1] - it[0] + 2 * max_walk for it in o.by_day[d]) for o in ordered[g]] + [0.0])
                fill[g][d] = fill[g + 1][d] + most
        return fill

    @staticmethod
    def _collapse(opts: list[Option]) -> list[Option]:
        """Merge options with the same footprint; the cheapest represents the rest."""
        reps: dict[tuple, Option] = {}
        for o in sorted(opts, key=lambda o: (o.static_total, not o.is_open, o.course_id, o.section_ids[0])):
            key = (o.sig, o.course.credits)
            if key in reps:
                reps[key].alternates.append(o)
            else:
                o.alternates = []
                reps[key] = o
        return list(reps.values())

    def _add_variants(self, sched: Schedule, opts: list[Option]) -> None:
        count = 1
        for i, o in enumerate(opts):
            count *= 1 + len(o.alternates)
            for alt in o.alternates:
                v = self._score(opts[:i] + [alt] + opts[i + 1:], detail=True)
                if v is not None:
                    sched.variants.append(v)
        sched.variants.sort(key=lambda v: v.cost)
        sched.variant_count = count - 1

    def diagnose(self, group_options: list[list[Option]]) -> list[str]:
        """Explain why nothing fit: pairs of required courses that can never coexist."""
        notes = []
        required = [(g, opts) for g, opts in zip(self.groups, group_options) if not g.optional]
        for (g1, o1), (g2, o2) in itertools.combinations(required, 2):
            if not any(self.compatible(a, b) for a in o1 for b in o2):
                n1 = "|".join(a.course_id for a in g1.alternatives)
                n2 = "|".join(a.course_id for a in g2.alternatives)
                notes.append(f"{n1} and {n2} conflict in every combination of their remaining sections")
        if not notes:
            notes.append(
                "every pair of courses fits on its own, but no full combination works "
                "(try relaxing --max-days, credit limits, --require-lunch, or section filters)"
            )
        return notes

    # ----- scoring -------------------------------------------------------

    def prepare(self, o: Option) -> None:
        """Precompute what scoring needs about one option."""
        prefs, w = self.prefs, self.prefs.weights
        static: dict[str, float] = {}
        if self.groups[o.group_index].optional:
            static["optional"] = w["optional_course"]
        if not o.is_open:
            waitlist = min(s.waitlist or 0 for s in o.sections)
            static["seats"] = w["closed"] + min(waitlist, 30) * w["waitlist_person"]
        names = [i.lower() for i in o.instructors]
        if prefs.prefer_profs and any(p in n for p in prefs.prefer_profs for n in names):
            static["preferred prof"] = w["preferred_prof"]
        if prefs.use_ratings:
            known = [r for r in (self.ratings.get(i) for i in o.instructors) if r is not None]
            rating = sum(known) / len(known) if known else 3.0
            static["ratings"] = (5 - rating) * w["rating"]
        o.static = static
        o.static_total = sum(static.values())
        o.by_day = [[] for _ in range(7)]
        o.day_mask = 0
        for m in o.timed:
            bldg = m.building.upper() if m.in_person and m.building else None
            for d in m.days:
                o.by_day[d].append((m.start, m.end, bldg, m, o.course_id))
                if m.in_person:
                    o.day_mask |= 1 << d
        for items in o.by_day:
            items.sort(key=lambda t: t[0])
        o.sig = tuple(sorted((m.days, m.start, m.end, m.building or "") for m in o.timed))

    def _option_heuristic(self, o: Option) -> float:
        prefs, w = self.prefs, self.prefs.weights
        h = o.static_total + bin(o.day_mask).count("1") * w["day"]
        for m in o.timed:
            h += len(m.days) * (
                max(0, prefs.preferred_start - m.start) * w["early_hour"] / 60
                + max(0, m.end - prefs.preferred_end) * w["late_hour"] / 60
            )
        return h

    def _day_eval(self, items: list[tuple], fill: float | None = None) -> tuple[float | None, float | None]:
        """Score one weekday's meetings (sorted by start time).

        Returns (exact cost, lower bound for any schedule that adds classes).
        Campus days, early/late penalties and a missing lunch can only get
        worse as classes are added; lunch is checked on raw gaps (ignoring
        walking) for the bound so it stays monotone. Idle time can shrink,
        but by at most `fill` minutes. Either value is None when
        --require-lunch can't be met; the bound is only computed when
        `fill` is given. This is the search's innermost loop, hence the
        plain style.
        """
        (w_day, w_early, w_late, w_idle, w_nolunch, w_tight, w_walk,
         pstart, pend, lunch_lo, lunch_hi, need, buffer, lunch_on, require) = self._k
        walk_cache = self._walk_cache
        first = items[0]
        start = first[0]
        end = first[1]
        campus = first[2] is not None
        best_lunch = start - lunch_lo if start < lunch_hi else lunch_hi - lunch_lo
        raw_lunch = best_lunch
        lunch_between = False
        idle = walk_total = tight = 0.0
        prev = first
        for b in items[1:]:
            b0, b1, b2 = b[0], b[1], b[2]
            a1, a2 = prev[1], prev[2]
            if b2 is not None:
                campus = True
            gap = b0 - a1
            if a2 is None or b2 is None or a2 == b2:
                walk = 0.0
            else:
                walk = walk_cache.get((a2, b2))
                if walk is None:
                    walk = self._walk(a2, b2)
            walk_total += walk
            if gap - walk < buffer:
                tight += 2 if gap < walk else 1
            lo = a1 + (walk if walk < gap else gap)
            idle += b0 - lo
            span = (b0 if b0 < lunch_hi else lunch_hi) - (lo if lo > lunch_lo else lunch_lo)
            if span > best_lunch:
                best_lunch, lunch_between = span, True
            if fill is not None:
                raw = (b0 if b0 < lunch_hi else lunch_hi) - (a1 if a1 > lunch_lo else lunch_lo)
                if raw > raw_lunch:
                    raw_lunch = raw
            if b1 > end:
                end = b1
            prev = b
        tail = lunch_hi - (end if end > lunch_lo else lunch_lo)
        if tail > best_lunch:
            best_lunch, lunch_between = tail, False
        if tail > raw_lunch:
            raw_lunch = tail

        fixed = w_day if campus else 0.0
        if start < pstart:
            fixed += (pstart - start) * w_early
        if end > pend:
            fixed += (end - pend) * w_late
        exact = fixed + tight * w_tight + walk_total * w_walk
        exact_idle = idle
        lunch_ok = True
        if lunch_on:
            if best_lunch >= need:
                if lunch_between:  # time spent eating isn't wasted time
                    exact_idle = idle - need if idle > need else 0.0
            else:
                lunch_ok = False
                exact += w_nolunch
        exact_val = None if (require and not lunch_ok) else exact + exact_idle * w_idle
        if fill is None:
            return exact_val, None
        bound = fixed
        if lunch_on:
            if raw_lunch < need:
                if require:
                    return exact_val, None
                bound += w_nolunch
            idle -= need
        if idle > fill:
            bound += (idle - fill) * w_idle
        return exact_val, bound

    def _expand_last(self, chosen: list[Option], allowed: int, optional: bool, fill: list[float],
                     options: list[Option], static: float):
        """Score every completion of `chosen` by one option of the last group.

        Per-day costs of the partial schedule are computed once and only the
        days the added option meets on are rescored. Returns the partial
        schedule's lower bound (None if --require-lunch can't be met) and a
        generator of (cost or None, option or None) for the children.
        """
        by_day: list[list[tuple]] = [[] for _ in range(7)]
        for o in chosen:
            for d, its in enumerate(o.by_day):
                if its:
                    by_day[d].extend(its)
        base = [0.0] * 7
        bad_days = set()  # days that break --require-lunch unless the new class changes them
        bound = 0.0
        for d in range(7):
            if by_day[d]:
                by_day[d].sort(key=lambda t: t[0])
                exact, b = self._day_eval(by_day[d], fill[d])
                if b is None:
                    return None, iter(())
                if exact is None:
                    bad_days.add(d)
                else:
                    base[d] = exact
                bound += b
        base_total = sum(base)

        def gen():
            if optional:
                yield (None if bad_days else base_total + static), None
            mask, k = allowed, 0
            while mask:
                if mask & 1:
                    opt = options[k]
                    touched = [d for d in range(7) if opt.by_day[d]]
                    total = None if bad_days - set(touched) else base_total
                    for d in touched if total is not None else ():
                        its = opt.by_day[d]
                        merged = sorted(by_day[d] + its, key=lambda t: t[0]) if by_day[d] else its
                        exact, _ = self._day_eval(merged)
                        if exact is None:
                            total = None
                            break
                        total += exact - base[d]
                    yield (None if total is None else total + static + opt.static_total), opt
                mask >>= 1
                k += 1

        return bound, gen()

    def evaluate(self, options: list[Option]) -> Schedule | None:
        """Score a complete schedule in detail; None if it breaks --require-lunch."""
        for o in options:
            if not o.by_day:
                self.prepare(o)
        self._constants()
        return self._score(options, detail=True)

    def _score(self, options: list[Option], detail: bool = False):
        """Cost of a complete schedule (a float), or a full Schedule when `detail`."""
        prefs, w = self.prefs, self.prefs.weights
        lunch_lo, lunch_hi = prefs.lunch_window
        cost = sum(o.static_total for o in options)
        campus_days, class_days, no_lunch, tight = [], [], [], []
        early = late = idle_total = walk_total = tight_units = 0.0
        for d in range(7):
            items = sorted((it for o in options for it in o.by_day[d]), key=lambda t: t[0])
            if not items:
                continue
            exact, _ = self._day_eval(items)
            if exact is None:
                return None
            cost += exact
            if not detail:
                continue
            # Recompute the pieces for the explanation shown to the user.
            class_days.append(d)
            if any(it[2] is not None for it in items):
                campus_days.append(d)
            start, end = items[0][0], max(it[1] for it in items)
            early += max(0, prefs.preferred_start - start)
            late += max(0, end - prefs.preferred_end)
            # Same rules as _day_eval: lunch before class, then in a gap, then after.
            best_lunch = min(start, lunch_hi) - lunch_lo
            lunch_between = False
            idle = 0.0
            for a, b in zip(items, items[1:]):
                gap, walk = b[0] - a[1], self._walk(a[2], b[2])
                walk_total += walk
                if gap - walk < prefs.min_buffer:
                    tight.append(Transition(d, a[3], b[3], a[4], b[4], gap, walk))
                    tight_units += 2 if gap < walk else 1
                lo = a[1] + min(walk, gap)
                idle += b[0] - lo
                span = min(b[0], lunch_hi) - max(lo, lunch_lo)
                if span > best_lunch:
                    best_lunch, lunch_between = span, True
            if lunch_hi - max(end, lunch_lo) > best_lunch:
                best_lunch, lunch_between = lunch_hi - max(end, lunch_lo), False
            if prefs.lunch:
                if best_lunch >= prefs.lunch_minutes:
                    if lunch_between:
                        idle = max(0.0, idle - prefs.lunch_minutes)
                else:
                    no_lunch.append(d)
            idle_total += idle
        cost = round(cost, 2)
        if not detail:
            return cost

        parts = {
            "days": len(campus_days) * w["day"],
            "early": early / 60 * w["early_hour"],
            "late": late / 60 * w["late_hour"],
            "idle": idle_total / 60 * w["idle_hour"],
            "no lunch": len(no_lunch) * w["no_lunch"],
            "tight": tight_units * w["tight"],
            "walking": walk_total * w["walk_minute"],
        }
        for o in options:
            for key, value in o.static.items():
                parts[key] = parts.get(key, 0.0) + value
        return Schedule(
            options=sorted(options, key=lambda o: o.course_id),
            cost=cost,
            breakdown={k: round(v, 2) for k, v in parts.items() if abs(v) >= 0.005},
            campus_days=campus_days,
            class_days=class_days,
            idle_minutes=idle_total,
            walk_minutes=walk_total,
            first_start=min((it[0] for o in options for items in o.by_day for it in items), default=None),
            last_end=max((it[1] for o in options for items in o.by_day for it in items), default=None),
            credits=sum(o.course.credits for o in options),
            tight=tight,
            no_lunch_days=no_lunch,
            optional_included=sum(1 for o in options if "optional" in o.static),
        )
