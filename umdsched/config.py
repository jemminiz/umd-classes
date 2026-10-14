"""Turn a TOML config file and command-line options into Preferences.

Precedence: built-in defaults < --style preset < config file < command line.
"""

from __future__ import annotations

from pathlib import Path

from umdsched.prefs import DEFAULT_WEIGHTS, Preferences
from umdsched.timeutil import parse_block, parse_days, parse_time, parse_time_range

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        tomllib = None  # type: ignore[assignment]

MPH_TO_M_PER_MIN = 26.8224

SAMPLE_CONFIG = """\
# umd-sched config. Run:  umd-sched build -c schedule.toml
# Anything here can also be given (or overridden) on the command line.

term = "{term}"          # e.g. "fall2026", "spring2027", or "202701"
style = "balanced"        # balanced | compact | relaxed | night-owl | early-bird

# Course specs:
#   "CMSC216"               any section
#   "CMSC216:0101,0102"     only these sections ("01*" wildcards, "~0201" to exclude)
#   "CMSC216@Herman"        only this professor ("@~Name" to avoid)
#   "ENGL101|ENGL102"       exactly one of these
#   "STAT400?"              optional: added only if it fits
courses = [
    "CMSC216",
    "CMSC250",
    "MATH240",
    "ENGL101|ENGL102",
]

[constraints]             # hard rules: schedules breaking these are never shown
# earliest = "9:00"       # no class starting before
# latest = "18:00"        # no class ending after
# days_off = "F"          # no classes on these days
# max_days = 4            # at most this many days on campus
# blocks = ["TuTh 15:00-18:00"]   # busy times (work, practice, ...)
# open_only = true        # only sections with open seats
# max_waitlist = 10
# avoid_profs = ["Smith"]
# min_credits = 12
# max_credits = 17
# min_gap = 0             # minimum minutes between back-to-back classes
# delivery = ["f2f", "blended", "online"]
# allow_unwalkable = false  # true keeps schedules where you can't walk there in time

[walking]
speed_mph = 3.0           # 2.5 = leisurely, 3.5 = brisk
path_factor = 1.3         # real paths vs. straight-line distance
unknown_minutes = 8       # assumed walk to/from a building not in the table
min_buffer = 3            # minutes of slack you want after arriving

[lunch]
enabled = true
window = "11:00-14:00"
minutes = 30
required = false          # true = reject schedules without a lunch break every day

[preferences]             # soft: these shape the ranking
preferred_start = "9:00"
preferred_end = "17:00"
# prefer_profs = ["Herman"]
ratings = false           # use PlanetTerp professor ratings in ranking

[weights]                 # penalty points; higher = cares more
# day = 10
# idle_hour = 6
# early_hour = 8
# late_hour = 4
# no_lunch = 8
# tight = 4
# walk_minute = 0.1
# closed = 15
# waitlist_person = 0.5
# rating = 4
# preferred_prof = -8
# optional_course = -40
"""


def _as_list(v) -> list:
    if v is None:
        return []
    return list(v) if isinstance(v, (list, tuple)) else [v]


def _time(v) -> int:
    return parse_time(str(v))


def apply_settings(prefs: Preferences, s: dict) -> None:
    """Apply a flat dict of settings (from config or CLI) to prefs. None values are skipped."""
    s = {k: v for k, v in s.items() if v is not None}
    if "earliest" in s:
        prefs.earliest = _time(s["earliest"])
    if "latest" in s:
        prefs.latest = _time(s["latest"])
    if "days_off" in s:
        prefs.days_off |= set(parse_days("".join(_as_list(s["days_off"]))))
    if "max_days" in s:
        prefs.max_days = int(s["max_days"])
    for b in _as_list(s.get("blocks")):
        prefs.blocks.append(parse_block(b))
    if "open_only" in s:
        prefs.open_only = bool(s["open_only"])
    if "max_waitlist" in s:
        prefs.max_waitlist = int(s["max_waitlist"])
    prefs.avoid_profs += [p.lower() for p in _as_list(s.get("avoid_profs"))]
    if "min_credits" in s:
        prefs.min_credits = int(s["min_credits"])
    if "max_credits" in s:
        prefs.max_credits = int(s["max_credits"])
    if "min_gap" in s:
        prefs.min_gap = int(s["min_gap"])
    if "delivery" in s:
        modes = {m.strip().lower() for v in _as_list(s["delivery"]) for m in str(v).split(",") if m.strip()}
        bad = modes - {"f2f", "blended", "online"}
        if bad:
            raise ValueError(f"unknown delivery mode(s) {', '.join(sorted(bad))} (use f2f, blended, online)")
        prefs.delivery = modes
    if "allow_unwalkable" in s:
        prefs.allow_unwalkable = bool(s["allow_unwalkable"])
    if "speed_mph" in s:
        prefs.walk_speed = float(s["speed_mph"]) * MPH_TO_M_PER_MIN
    if "path_factor" in s:
        prefs.path_factor = float(s["path_factor"])
    if "unknown_minutes" in s:
        prefs.unknown_walk = float(s["unknown_minutes"])
    if "min_buffer" in s:
        prefs.min_buffer = int(s["min_buffer"])
    if "lunch" in s:
        prefs.lunch = bool(s["lunch"])
    if "lunch_window" in s:
        prefs.lunch_window = parse_time_range(str(s["lunch_window"]))
    if "lunch_minutes" in s:
        prefs.lunch_minutes = int(s["lunch_minutes"])
    if "require_lunch" in s:
        prefs.require_lunch = bool(s["require_lunch"])
        if prefs.require_lunch:
            prefs.lunch = True
    if "preferred_start" in s:
        prefs.preferred_start = _time(s["preferred_start"])
    if "preferred_end" in s:
        prefs.preferred_end = _time(s["preferred_end"])
    prefs.prefer_profs += [p.lower() for p in _as_list(s.get("prefer_profs"))]
    if "ratings" in s:
        prefs.use_ratings = bool(s["ratings"])
    for key, value in (s.get("weights") or {}).items():
        if key not in DEFAULT_WEIGHTS:
            raise ValueError(f"unknown weight {key!r} (known: {', '.join(DEFAULT_WEIGHTS)})")
        prefs.weights[key] = float(value)


def load_config(path: Path) -> dict:
    if tomllib is None:
        raise RuntimeError("reading config files needs Python 3.11+ or `pip install tomli`")
    with open(path, "rb") as f:
        return tomllib.load(f)


def flatten_config(cfg: dict) -> dict:
    """Map the sectioned config layout onto apply_settings' flat keys."""
    flat: dict = {}
    flat.update(cfg.get("constraints") or {})
    walking = cfg.get("walking") or {}
    for k in ("speed_mph", "path_factor", "unknown_minutes", "min_buffer"):
        if k in walking:
            flat[k] = walking[k]
    lunch = cfg.get("lunch") or {}
    for src, dst in (("enabled", "lunch"), ("window", "lunch_window"), ("minutes", "lunch_minutes"), ("required", "require_lunch")):
        if src in lunch:
            flat[dst] = lunch[src]
    flat.update(cfg.get("preferences") or {})
    if cfg.get("weights"):
        flat["weights"] = cfg["weights"]
    return flat
