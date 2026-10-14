"""Scheduling preferences: hard constraints, soft preferences and weights."""

from __future__ import annotations

from dataclasses import dataclass, field

# Penalty points; a schedule's cost is the sum, lower is better.
DEFAULT_WEIGHTS: dict[str, float] = {
    "day": 10.0,  # per day you have to be on campus
    "idle_hour": 6.0,  # per hour of dead time between classes (after walking and lunch)
    "early_hour": 8.0,  # per hour a class starts before preferred_start, per day
    "late_hour": 4.0,  # per hour a class ends after preferred_end, per day
    "no_lunch": 8.0,  # per campus day without a lunch-sized break in the lunch window
    "tight": 4.0,  # per transition with less than min_buffer minutes of slack after walking
    "walk_minute": 0.1,  # per minute walked per week
    "closed": 15.0,  # per section with no open seats
    "waitlist_person": 0.5,  # per person ahead of you on a waitlist (capped at 30)
    "rating": 4.0,  # per rating point below 5 (only with --ratings)
    "preferred_prof": -8.0,  # bonus for a section taught by a preferred professor
    "optional_course": -40.0,  # bonus for fitting in an optional course
}

STYLES: dict[str, dict] = {
    "balanced": {},
    "compact": {"weights": {"day": 20.0, "idle_hour": 10.0, "no_lunch": 4.0}},
    "relaxed": {
        "weights": {"day": 5.0, "idle_hour": 3.0, "early_hour": 14.0, "no_lunch": 15.0, "tight": 10.0},
        "min_buffer": 5,
    },
    "night-owl": {"preferred_start": 11 * 60, "preferred_end": 20 * 60, "weights": {"early_hour": 15.0}},
    "early-bird": {"preferred_start": 8 * 60, "preferred_end": 15 * 60, "weights": {"late_hour": 10.0}},
}


@dataclass
class Preferences:
    # Hard constraints: schedules that break these are never shown.
    earliest: int | None = None  # no class may start before this
    latest: int | None = None  # no class may end after this
    days_off: set[int] = field(default_factory=set)
    max_days: int | None = None  # at most this many days on campus
    blocks: list[tuple[tuple[int, ...], int, int]] = field(default_factory=list)  # busy times
    open_only: bool = False
    max_waitlist: int | None = None
    delivery: set[str] = field(default_factory=lambda: {"f2f", "blended", "online"})
    avoid_profs: list[str] = field(default_factory=list)
    min_credits: int | None = None
    max_credits: int | None = None
    min_gap: int = 0  # minimum minutes between consecutive classes
    allow_unwalkable: bool = False  # keep schedules where the walk takes longer than the gap

    # Walking
    walk_speed: float = 80.0  # meters per minute (~3 mph)
    path_factor: float = 1.3
    unknown_walk: float = 8.0
    min_buffer: int = 3  # slack you'd like after walking before class starts

    # Lunch
    lunch: bool = True
    lunch_window: tuple[int, int] = (11 * 60, 14 * 60)
    lunch_minutes: int = 30
    require_lunch: bool = False

    # Soft preferences
    preferred_start: int = 9 * 60
    preferred_end: int = 17 * 60
    prefer_profs: list[str] = field(default_factory=list)
    use_ratings: bool = False

    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))

    def apply_style(self, name: str) -> None:
        if name not in STYLES:
            raise ValueError(f"unknown style {name!r} (choose from {', '.join(STYLES)})")
        for key, value in STYLES[name].items():
            if key == "weights":
                self.weights.update(value)
            else:
                setattr(self, key, value)
