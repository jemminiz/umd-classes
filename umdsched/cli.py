"""Command-line interface for umd-sched."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from umdsched import __version__
from umdsched.buildings import WalkModel, fetch_umdio_buildings
from umdsched.config import MPH_TO_M_PER_MIN, SAMPLE_CONFIG, apply_settings, flatten_config, load_config
from umdsched.prefs import DEFAULT_WEIGHTS, STYLES, Preferences
from umdsched.sources import CourseLoader, DataError, default_cache_dir, default_config_dir, save_data_file
from umdsched.specs import parse_spec
from umdsched.terms import approx_term_dates, default_term, parse_term, term_name

COMMANDS = ("build", "show", "walk", "buildings", "fetch", "init-config")

EPILOG = """\
course specs:
  CMSC216               any section
  CMSC216:0101,0102     only these sections (01* wildcards, ~0201 to exclude)
  CMSC216@Herman        only sections taught by Herman (@~Herman to avoid)
  "ENGL101|ENGL102"     exactly one of these courses
  STAT400?              optional -- included only if it fits (or use --optional)

examples:
  umd-sched CMSC216 CMSC250 MATH240 "ENGL101|ENGL102"
  umd-sched CMSC216@Herman MATH240:01* --days-off F --earliest 9:00
  umd-sched -c schedule.toml --style compact --ics fall.ics
"""


def _common(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("data")
    g.add_argument("-t", "--term", help="term, e.g. fall2026, spring27, 202701 (default: the term you're registering for)")
    g.add_argument("--source", choices=["testudo", "umdio"], default="testudo", help="where to get sections (default: testudo)")
    g.add_argument("--data", action="append", type=Path, metavar="FILE", help="load courses from a JSON file made by `fetch`")
    g.add_argument("--refresh", action="store_true", help="ignore cached data and re-download")
    g.add_argument("--offline", action="store_true", help="only use cached data (any age)")
    g.add_argument("--cache-minutes", type=float, default=30, help="how long cached seat data stays fresh (default: 30)")
    g.add_argument("--cache-dir", type=Path, default=None, help=argparse.SUPPRESS)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="umd-sched",
        description="Find the most efficient UMD class schedules (a command-line Venus).",
        epilog="Run `umd-sched <command> -h` for details. With no command, `build` is assumed.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"umd-sched {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="command")

    # ----- build -----
    b = sub.add_parser(
        "build",
        help="generate and rank schedules",
        description="Generate every conflict-free schedule for your courses and show the best ones.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    b.add_argument("courses", nargs="*", metavar="COURSE", help="course specs (see below)")
    b.add_argument("-c", "--config", type=Path, help="TOML config file (see `umd-sched init-config`)")
    b.add_argument("--optional", action="append", default=[], metavar="COURSE", help="a course to include only if it fits")
    b.add_argument("--style", choices=list(STYLES), help="preset ranking style (default: balanced)")
    _common(b)

    h = b.add_argument_group("hard constraints (schedules breaking these are dropped)")
    h.add_argument("--earliest", metavar="TIME", help="no class starting before TIME, e.g. 9:00 or 10am")
    h.add_argument("--latest", metavar="TIME", help="no class ending after TIME, e.g. 17:00 or 5pm")
    h.add_argument("--days-off", metavar="DAYS", help="no classes on these days, e.g. F or MF")
    h.add_argument("--max-days", type=int, metavar="N", help="at most N days on campus")
    h.add_argument("--block", action="append", dest="blocks", metavar="'DAYS START-END'", help="busy time, e.g. 'TuTh 3pm-6pm' (repeatable)")
    h.add_argument("--open-only", action="store_true", default=None, help="only sections with open seats")
    h.add_argument("--max-waitlist", type=int, metavar="N", help="skip full sections with more than N on the waitlist")
    h.add_argument("--avoid-prof", action="append", dest="avoid_profs", metavar="NAME", help="never take a section taught by NAME (repeatable)")
    h.add_argument("--credits", metavar="MIN-MAX", help="total credit range, e.g. 12-16 (useful with optional courses)")
    h.add_argument("--min-gap", type=int, metavar="MIN", help="minimum minutes between consecutive classes (default: 0)")
    h.add_argument("--in-person", action="store_true", help="exclude fully online sections")
    h.add_argument("--delivery", metavar="MODES", help="allowed delivery modes: f2f,blended,online (default: all)")
    h.add_argument("--allow-unwalkable", action="store_true", default=None, help="keep schedules where a walk takes longer than the gap (flagged)")

    w = b.add_argument_group("walking")
    w.add_argument("--walk-speed", type=float, dest="speed_mph", metavar="MPH", help="walking speed in mph (default: 3.0)")
    w.add_argument("--path-factor", type=float, metavar="X", help="real path length vs straight line (default: 1.3)")
    w.add_argument("--unknown-walk", type=float, dest="unknown_minutes", metavar="MIN", help="walk time assumed for unmapped buildings (default: 8)")
    w.add_argument("--min-buffer", type=int, metavar="MIN", help="slack wanted after walking; less is flagged 'tight' (default: 3)")
    w.add_argument("--buildings", type=Path, metavar="FILE", help="JSON file of building coordinates to add/override")

    lu = b.add_argument_group("lunch")
    lu.add_argument("--lunch", dest="lunch_window", metavar="START-END", help="window for a lunch break (default: 11:00-14:00)")
    lu.add_argument("--lunch-length", type=int, dest="lunch_minutes", metavar="MIN", help="minutes needed for lunch (default: 30)")
    lu.add_argument("--require-lunch", action="store_true", default=None, help="drop schedules without a lunch break every class day")
    lu.add_argument("--no-lunch", action="store_false", dest="lunch", default=None, help="don't care about lunch")

    pr = b.add_argument_group("soft preferences (shape the ranking)")
    pr.add_argument("--prefer-start", dest="preferred_start", metavar="TIME", help="classes before this are penalized (default: 9:00)")
    pr.add_argument("--prefer-end", dest="preferred_end", metavar="TIME", help="classes after this are penalized (default: 17:00)")
    pr.add_argument("--prefer-prof", action="append", dest="prefer_profs", metavar="NAME", help="favor sections taught by NAME (repeatable)")
    pr.add_argument("--ratings", action="store_true", default=None, help="favor better-rated professors (PlanetTerp)")
    pr.add_argument("--weight", action="append", default=[], metavar="KEY=VALUE", help=f"tune a penalty weight; keys: {', '.join(DEFAULT_WEIGHTS)}")

    o = b.add_argument_group("output")
    o.add_argument("-n", "--count", type=int, default=5, help="how many schedules to show (default: 5)")
    o.add_argument("--format", choices=["text", "json"], default="text")
    o.add_argument("--no-grid", action="store_true", help="don't draw the weekly grid")
    o.add_argument("--ics", type=Path, metavar="FILE", help="export a schedule to an iCalendar file")
    o.add_argument("--pick", type=int, default=1, metavar="N", help="which schedule --ics exports (default: 1)")
    o.add_argument("--term-start", type=dt.date.fromisoformat, metavar="YYYY-MM-DD", help="first day of classes (for --ics)")
    o.add_argument("--term-end", type=dt.date.fromisoformat, metavar="YYYY-MM-DD", help="last day of classes (for --ics)")
    o.add_argument("--max-schedules", type=int, default=300_000, help=argparse.SUPPRESS)

    # ----- show -----
    s = sub.add_parser("show", help="list a course's sections, times, professors and seats")
    s.add_argument("courses", nargs="+", metavar="COURSE")
    s.add_argument("--open-only", action="store_true", help="only sections with open seats")
    s.add_argument("--ratings", action="store_true", help="show PlanetTerp professor ratings")
    _common(s)

    # ----- walk -----
    wk = sub.add_parser("walk", help="estimate the walk between two buildings, e.g. `walk IRB ESJ`")
    wk.add_argument("frm", metavar="FROM")
    wk.add_argument("to", metavar="TO")
    wk.add_argument("--walk-speed", type=float, default=3.0, metavar="MPH")
    wk.add_argument("--path-factor", type=float, default=1.3)
    wk.add_argument("--buildings", type=Path, metavar="FILE")

    # ----- buildings -----
    bl = sub.add_parser("buildings", help="list known building codes, or refresh coordinates from umd.io")
    bl.add_argument("search", nargs="?", help="filter by code or name")
    bl.add_argument("--refresh", action="store_true", help="download exact coordinates from umd.io")

    # ----- fetch -----
    f = sub.add_parser("fetch", help="download courses to a JSON file for offline use or sharing")
    f.add_argument("courses", nargs="+", metavar="COURSE")
    f.add_argument("-o", "--output", type=Path, required=True)
    _common(f)

    # ----- init-config -----
    ic = sub.add_parser("init-config", help="write a commented sample config file")
    ic.add_argument("path", nargs="?", type=Path, default=Path("schedule.toml"))
    ic.add_argument("--force", action="store_true", help="overwrite an existing file")
    return parser


def _die(msg: str) -> int:
    print(f"umd-sched: error: {msg}", file=sys.stderr)
    return 2


def _loader(args, term: str) -> CourseLoader:
    return CourseLoader(
        term,
        source=args.source,
        cache_dir=args.cache_dir,
        ttl_minutes=args.cache_minutes,
        refresh=args.refresh,
        offline=args.offline,
        data_files=args.data,
    )


def _walk_model(buildings_file: Path | None = None, **kw) -> WalkModel:
    model = WalkModel(**kw)
    user_file = default_config_dir() / "buildings.json"
    if user_file.exists():
        model.load_overrides(user_file)
    if buildings_file:
        model.load_overrides(buildings_file)
    return model


def _ratings(names: list[str], args) -> dict:
    from umdsched.ratings import RatingCache

    return RatingCache(args.cache_dir or default_cache_dir(), offline=args.offline).get_many(names)


# ----- commands ----------------------------------------------------------


def cmd_build(args) -> int:
    cfg: dict = {}
    if args.config:
        try:
            cfg = load_config(args.config)
        except (OSError, ValueError, RuntimeError) as e:
            return _die(f"can't read config {args.config}: {e}")

    try:
        term = parse_term(args.term or cfg.get("term"))
        specs = list(cfg.get("courses") or []) + list(args.courses)
        groups = [parse_spec(x) for x in specs]
        groups += [parse_spec(x, optional=True) for x in list(cfg.get("optional") or []) + args.optional]
    except ValueError as e:
        return _die(str(e))
    if not groups:
        return _die("no courses given (e.g. `umd-sched CMSC216 MATH240`, or -c schedule.toml)")

    prefs = Preferences()
    try:
        prefs.apply_style(args.style or cfg.get("style") or "balanced")
        apply_settings(prefs, flatten_config(cfg))
        cli = {k: getattr(args, k) for k in (
            "earliest", "latest", "days_off", "max_days", "blocks", "open_only", "max_waitlist",
            "avoid_profs", "min_gap", "allow_unwalkable", "speed_mph", "path_factor",
            "unknown_minutes", "min_buffer", "lunch", "lunch_window", "lunch_minutes",
            "require_lunch", "preferred_start", "preferred_end", "prefer_profs", "ratings", "delivery",
        )}
        if args.in_person:
            cli["delivery"] = "f2f,blended"
        if args.credits:
            lo, _, hi = args.credits.partition("-")
            cli["min_credits"] = int(lo) if lo.strip() else None
            cli["max_credits"] = int(hi) if hi.strip() else None
        weights = {}
        for item in args.weight:
            key, sep, value = item.partition("=")
            if not sep:
                raise ValueError(f"--weight expects KEY=VALUE, got {item!r}")
            weights[key.strip()] = float(value)
        cli["weights"] = weights
        apply_settings(prefs, cli)
    except ValueError as e:
        return _die(str(e))

    course_ids = [a.course_id for g in groups for a in g.alternatives]
    try:
        courses = _loader(args, term).load(course_ids)
    except DataError as e:
        return _die(str(e))

    walk = _walk_model(
        buildings_file=args.buildings,
        speed_m_per_min=prefs.walk_speed,
        path_factor=prefs.path_factor,
        unknown_min=prefs.unknown_walk,
    )
    ratings = None
    if prefs.use_ratings:
        ratings = _ratings([i for c in courses.values() for s in c.sections for i in s.instructors], args)

    from umdsched.scheduler import Scheduler

    scheduler = Scheduler(courses, groups, prefs, walk=walk, ratings=ratings)
    result = scheduler.search(keep=max(args.count, args.pick, 1), max_schedules=args.max_schedules)

    if args.format == "json":
        from umdsched.render import schedule_to_dict

        print(json.dumps({
            "term": term,
            "total_found": result.total_found,
            "truncated": result.truncated,
            "schedules": [schedule_to_dict(s) for s in result.schedules[: args.count]],
            "diagnosis": result.diagnosis,
        }, indent=2))
    else:
        _print_build(result, groups, courses, term, args, ratings)

    if args.ics and result.schedules:
        if not 1 <= args.pick <= len(result.schedules):
            return _die(f"--pick {args.pick} is out of range (found {len(result.schedules)})")
        from umdsched.ics import schedule_to_ics

        start, end = approx_term_dates(term)
        start, end = args.term_start or start, args.term_end or end
        args.ics.write_text(schedule_to_ics(result.schedules[args.pick - 1], start, end, term_name(term)))
        approx = "" if args.term_start and args.term_end else " (approximate term dates; set --term-start/--term-end)"
        print(f"\nWrote schedule #{args.pick} to {args.ics}: {start} to {end}{approx}", file=sys.stderr)
    return 0 if result.schedules else 1


def _print_build(result, groups, courses, term, args, ratings) -> None:
    from umdsched.render import render_schedule

    print(f"{term_name(term)} · " + ", ".join(g.describe() for g in groups))
    for cid, course in courses.items():
        removed = result.filter_report.get(cid)
        if removed:
            why = ", ".join(f"{n} by {r}" for r, n in removed.most_common())
            print(f"  {cid}: {len(course.sections) - sum(removed.values())}/{len(course.sections)} sections kept ({why} removed)")
    if not result.schedules:
        print("\nNo schedule fits all your constraints:")
        for line in result.diagnosis:
            print(f"  - {line}")
        return
    if result.truncated:
        found = f"Scored {result.total_found:,}+ schedules before hitting the search limit (add constraints to narrow it)"
    else:
        found = f"Found {'about ' if not result.exact else ''}{result.total_found:,} possible schedules"
    shown = min(args.count, len(result.schedules))
    print(f"{found}; showing the best {shown}.\n")
    for i, sched in enumerate(result.schedules[: args.count], 1):
        print(render_schedule(i, sched, ratings=ratings, grid=not args.no_grid))
        print()


def cmd_show(args) -> int:
    from umdsched.render import render_course

    try:
        term = parse_term(args.term)
        courses = _loader(args, term).load(args.courses)
    except (ValueError, DataError) as e:
        return _die(str(e))
    ratings = None
    if args.ratings:
        ratings = _ratings([i for c in courses.values() for s in c.sections for i in s.instructors], args)
    print(term_name(term))
    for course in courses.values():
        if args.open_only:
            course.sections = [s for s in course.sections if s.is_open]
        print(render_course(course, ratings=ratings))
        print()
    return 0


def cmd_walk(args) -> int:
    model = _walk_model(buildings_file=args.buildings, speed_m_per_min=args.walk_speed * MPH_TO_M_PER_MIN, path_factor=args.path_factor)
    a, b = args.frm.upper(), args.to.upper()
    for code in (a, b):
        if not model.known(code):
            return _die(f"unknown building {code!r} (see `umd-sched buildings`)")
    dist = model.distance_m(a, b)
    mins = model.minutes(a, b)
    print(f"{model.buildings[a][0]} ({a}) -> {model.buildings[b][0]} ({b})")
    print(f"  ~{dist * args.path_factor:.0f} m on foot, about {mins:.0f} min at {args.walk_speed:g} mph")
    return 0


def cmd_buildings(args) -> int:
    if args.refresh:
        try:
            data = fetch_umdio_buildings()
        except Exception as e:  # network, JSON, HTTP errors
            return _die(f"couldn't download buildings from umd.io: {e}")
        path = default_config_dir() / "buildings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=1))
        print(f"Saved {len(data)} buildings to {path}; they'll be used automatically.")
        return 0
    model = _walk_model()
    q = (args.search or "").lower()
    for code, (name, lat, lon) in sorted(model.buildings.items()):
        if q in code.lower() or q in name.lower():
            print(f"{code:<6}{name}")
    return 0


def cmd_fetch(args) -> int:
    try:
        term = parse_term(args.term)
        courses = _loader(args, term).load(args.courses)
    except (ValueError, DataError) as e:
        return _die(str(e))
    save_data_file(args.output, term, list(courses.values()), args.source)
    total = sum(len(c.sections) for c in courses.values())
    print(f"Saved {len(courses)} courses ({total} sections) for {term_name(term)} to {args.output}")
    return 0


def cmd_init_config(args) -> int:
    if args.path.exists() and not args.force:
        return _die(f"{args.path} already exists (use --force to overwrite)")
    args.path.write_text(SAMPLE_CONFIG.format(term=default_term()))
    print(f"Wrote {args.path}. Edit it, then run: umd-sched build -c {args.path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in COMMANDS and argv[0] not in ("-h", "--help", "--version"):
        argv.insert(0, "build")
    parser = build_parser()
    if not argv:
        parser.print_help()
        return 0
    args = parser.parse_args(argv)
    handlers = {
        "build": cmd_build,
        "show": cmd_show,
        "walk": cmd_walk,
        "buildings": cmd_buildings,
        "fetch": cmd_fetch,
        "init-config": cmd_init_config,
    }
    try:
        return handlers[args.command](args)
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:  # e.g. piped into `head`
        import os

        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0
