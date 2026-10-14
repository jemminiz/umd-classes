import datetime as dt
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from umdsched.buildings import WalkModel
from umdsched.ics import schedule_to_ics
from umdsched.models import Course, Meeting, Section
from umdsched.prefs import Preferences
from umdsched.scheduler import Scheduler
from umdsched.specs import parse_spec
from umdsched.timeutil import parse_days, parse_time

ROOT = Path(__file__).resolve().parent.parent


def mtg(days, start, end, building="ESJ", kind="Lecture"):
    return Meeting(parse_days(days), parse_time(start), parse_time(end), building, "0101", kind)


def course(cid, *sections, credits=3):
    c = Course(cid, f"{cid} title", credits)
    for sid, profs, meetings, *seats in sections:
        open_seats = seats[0] if seats else 5
        c.sections.append(Section(cid, sid, profs, meetings, 30, open_seats, 0 if open_seats else 9))
    return c


def run(courses, specs, prefs=None, **kw):
    groups = [parse_spec(s) for s in specs]
    sched = Scheduler({c.course_id: c for c in courses}, groups, prefs or Preferences(), **kw)
    return sched.search(keep=20)


class Conflicts(unittest.TestCase):
    def test_overlap_is_excluded(self):
        a = course("AAAA101", ("0101", ["X"], [mtg("MWF", "10:00am", "10:50am")]))
        b = course(
            "BBBB101",
            ("0101", ["Y"], [mtg("MWF", "10:30am", "11:20am")]),
            ("0201", ["Y"], [mtg("TuTh", "10:00am", "11:15am")]),
        )
        res = run([a, b], ["AAAA101", "BBBB101"])
        self.assertEqual(res.total_found, 1)
        self.assertEqual([o.section_ids for o in res.schedules[0].options], [["0101"], ["0201"]])

    def test_unwalkable_gap_is_excluded_unless_allowed(self):
        # PAC to IRB is a ~20 minute walk; a 10 minute gap can't cover it.
        a = course("AAAA101", ("0101", ["X"], [mtg("MWF", "10:00am", "10:50am", "PAC")]))
        b = course("BBBB101", ("0101", ["Y"], [mtg("MWF", "11:00am", "11:50am", "IRB")]))
        self.assertEqual(run([a, b], ["AAAA101", "BBBB101"]).total_found, 0)
        prefs = Preferences(allow_unwalkable=True)
        res = run([a, b], ["AAAA101", "BBBB101"], prefs)
        self.assertEqual(res.total_found, 1)
        self.assertTrue(res.schedules[0].tight)
        self.assertLess(res.schedules[0].tight[0].slack, 0)

    def test_same_building_back_to_back_is_fine(self):
        a = course("AAAA101", ("0101", ["X"], [mtg("MWF", "10:00am", "10:50am", "ESJ")]))
        b = course("BBBB101", ("0101", ["Y"], [mtg("MWF", "11:00am", "11:50am", "ESJ")]))
        res = run([a, b], ["AAAA101", "BBBB101"])
        self.assertEqual(res.total_found, 1)
        self.assertEqual(res.schedules[0].tight, [])

    def test_diagnosis_names_conflicting_pair(self):
        a = course("AAAA101", ("0101", ["X"], [mtg("MWF", "10:00am", "10:50am")]))
        b = course("BBBB101", ("0101", ["Y"], [mtg("MWF", "10:00am", "10:50am")]))
        res = run([a, b], ["AAAA101", "BBBB101"])
        self.assertEqual(res.schedules, [])
        self.assertIn("AAAA101 and BBBB101 conflict", res.diagnosis[0])

    def test_async_never_conflicts(self):
        a = course("AAAA101", ("0101", ["X"], [mtg("MWF", "10:00am", "10:50am")]))
        b = course("BBBB101", ("ESG1", [], [Meeting((), None, None, "ONLINE")]))
        self.assertEqual(run([a, b], ["AAAA101", "BBBB101"]).total_found, 1)


class Constraints(unittest.TestCase):
    def setUp(self):
        self.a = course(
            "AAAA101",
            ("0101", ["Early Prof"], [mtg("MWF", "8:00am", "8:50am")]),
            ("0201", ["Late Prof"], [mtg("TuTh", "2:00pm", "3:15pm")]),
            ("0301", ["Friday Prof"], [mtg("F", "11:00am", "1:30pm")], 0),
        )

    def ids(self, res):
        return sorted(o.section_ids[0] for s in res.schedules for o in s.options) if res.schedules else []

    def test_earliest_latest_days_off_block(self):
        self.assertEqual(self.ids(run([self.a], ["AAAA101"], Preferences(earliest=9 * 60))), ["0201", "0301"])
        self.assertEqual(self.ids(run([self.a], ["AAAA101"], Preferences(latest=14 * 60))), ["0101", "0301"])
        self.assertEqual(self.ids(run([self.a], ["AAAA101"], Preferences(days_off={4}))), ["0201"])
        prefs = Preferences(blocks=[((1, 3), 13 * 60, 15 * 60)])
        self.assertEqual(self.ids(run([self.a], ["AAAA101"], prefs)), ["0101", "0301"])

    def test_open_only_and_avoid_prof(self):
        self.assertEqual(self.ids(run([self.a], ["AAAA101"], Preferences(open_only=True))), ["0101", "0201"])
        self.assertEqual(self.ids(run([self.a], ["AAAA101"], Preferences(avoid_profs=["early"]))), ["0201", "0301"])
        res = run([self.a], ["AAAA101"], Preferences(open_only=True, earliest=9 * 60, latest=14 * 60))
        self.assertEqual(res.schedules, [])
        self.assertEqual(sum(res.filter_report["AAAA101"].values()), 3)

    def test_max_days(self):
        b = course("BBBB101", ("0101", ["Y"], [mtg("MW", "10:00am", "11:15am")]), ("0201", ["Y"], [mtg("TuTh", "11:00am", "12:15pm")]))
        res = run([self.a, b], ["AAAA101:0201", "BBBB101"], Preferences(max_days=2))
        self.assertEqual(res.total_found, 1)
        self.assertEqual(res.schedules[0].campus_days, [1, 3])

    def test_require_lunch(self):
        a = course("AAAA101", ("0101", ["X"], [mtg("MWF", "11:00am", "11:50am")]))
        b = course(
            "BBBB101",
            ("0101", ["Y"], [mtg("MWF", "12:00pm", "1:40pm")]),
            ("0201", ["Y"], [mtg("MWF", "3:00pm", "3:50pm")]),
        )
        res = run([a, b], ["AAAA101", "BBBB101"], Preferences(require_lunch=True))
        self.assertEqual(res.total_found, 1)
        self.assertEqual(res.schedules[0].options[1].section_ids, ["0201"])

    def test_credit_limits_with_optional(self):
        b = course("BBBB101", ("0101", ["Y"], [mtg("TuTh", "9:30am", "10:45am")]), credits=4)
        res = run([self.a, b], ["AAAA101", "BBBB101?"], Preferences(max_credits=3))
        self.assertTrue(res.schedules)
        self.assertTrue(all(len(s.options) == 1 for s in res.schedules))
        res = run([self.a, b], ["AAAA101", "BBBB101?"], Preferences(min_credits=7))
        self.assertTrue(all(s.credits == 7 for s in res.schedules))


class Ranking(unittest.TestCase):
    def test_prefers_fewer_days_and_no_8am(self):
        a = course(
            "AAAA101",
            ("0101", ["X"], [mtg("MWF", "8:00am", "8:50am")]),
            ("0201", ["X"], [mtg("TuTh", "11:00am", "12:15pm")]),
        )
        b = course("BBBB101", ("0101", ["Y"], [mtg("TuTh", "9:30am", "10:45am")]))
        res = run([a, b], ["AAAA101", "BBBB101"])
        self.assertEqual(res.schedules[0].options[0].section_ids, ["0201"])
        self.assertLess(res.schedules[0].cost, res.schedules[1].cost)

    def test_optional_course_included_when_it_fits(self):
        a = course("AAAA101", ("0101", ["X"], [mtg("TuTh", "11:00am", "12:15pm")]))
        b = course("BBBB101", ("0101", ["Y"], [mtg("TuTh", "2:00pm", "3:15pm")]))
        res = run([a, b], ["AAAA101", "BBBB101?"])
        self.assertEqual(len(res.schedules[0].options), 2)
        self.assertEqual(res.total_found, 2)

    def test_alternatives_pick_exactly_one(self):
        a = course("ENGL101", ("0101", ["X"], [mtg("TuTh", "11:00am", "12:15pm")]))
        b = course("ENGL102", ("0101", ["Y"], [mtg("TuTh", "2:00pm", "3:15pm")]))
        res = run([a, b], ["ENGL101|ENGL102"])
        self.assertEqual(res.total_found, 2)
        self.assertTrue(all(len(s.options) == 1 for s in res.schedules))

    def test_equivalent_sections_collapse_and_same_timetable_folds(self):
        same = [mtg("MWF", "10:00am", "10:50am")]
        a = course(
            "AAAA101",
            ("0101", ["X"], same, 0),
            ("0102", ["X"], same, 4),
            ("0201", ["Z"], same, 3),
        )
        res = run([a], ["AAAA101"])
        self.assertEqual(res.total_found, 1)  # one timetable...
        top = res.schedules[0]
        self.assertEqual(top.options[0].section_ids, ["0102", "0101"])  # open section listed first
        self.assertEqual(len(top.variants), 1)  # ...with professor Z's section as the alternative
        self.assertEqual(top.variants[0].options[0].instructors, ["Z"])
        self.assertEqual(top.variant_count, 1)

    def test_lunch_and_idle(self):
        a = course("AAAA101", ("0101", ["X"], [mtg("M", "10:00am", "10:50am")]))
        b = course("BBBB101", ("0101", ["Y"], [mtg("M", "2:00pm", "2:50pm")]))
        s = run([a, b], ["AAAA101", "BBBB101"]).schedules[0]
        self.assertEqual(s.no_lunch_days, [])
        self.assertEqual(s.idle_minutes, 190 - 30)  # lunch eaten in the gap isn't idle

    def test_preferred_prof_and_ratings(self):
        a = course(
            "AAAA101",
            ("0101", ["Alice Good"], [mtg("TuTh", "11:00am", "12:15pm")]),
            ("0201", ["Bob Meh"], [mtg("TuTh", "11:00am", "12:15pm")]),
        )
        res = run([a], ["AAAA101"], Preferences(prefer_profs=["bob"]))
        self.assertEqual(res.schedules[0].options[0].instructors, ["Bob Meh"])
        res = run([a], ["AAAA101"], Preferences(use_ratings=True), ratings={"Alice Good": 4.8, "Bob Meh": 2.1})
        self.assertEqual(res.schedules[0].options[0].instructors, ["Alice Good"])


class WalkingModel(unittest.TestCase):
    def test_estimates(self):
        w = WalkModel()
        self.assertEqual(w.minutes("IRB", "IRB"), 0)
        self.assertEqual(w.minutes("IRB", "ONLINE"), 0)
        self.assertEqual(w.minutes("IRB", "ZZZ"), w.unknown_min)
        near, far = w.minutes("IRB", "CSI"), w.minutes("IRB", "PAC")
        self.assertLess(near, 4)
        self.assertGreater(far, 15)
        self.assertAlmostEqual(w.minutes("ESJ", "MCK"), w.minutes("MCK", "ESJ"))

    def test_overrides(self):
        w = WalkModel()
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "b.json"
            p.write_text(json.dumps([{"code": "NEW", "name": "New Hall", "lat": 38.9890, "long": -76.9365}]))
            self.assertEqual(w.load_overrides(p), 1)
        self.assertLess(w.minutes("NEW", "IRB"), 2)


class Export(unittest.TestCase):
    def test_ics(self):
        a = course("AAAA101", ("0101", ["X"], [mtg("TuTh", "11:00am", "12:15pm", "ESJ")]))
        s = run([a], ["AAAA101"]).schedules[0]
        ics = schedule_to_ics(s, dt.date(2027, 1, 25), dt.date(2027, 5, 11))
        self.assertIn("DTSTART;TZID=America/New_York:20270126T110000", ics)  # first Tuesday
        self.assertIn("RRULE:FREQ=WEEKLY;BYDAY=TU,TH;UNTIL=20270512T035959Z", ics)
        self.assertIn("LOCATION:ESJ 0101", ics)
        self.assertTrue(all(line.endswith("\r") or not line for line in ics.split("\n")))


class CommandLine(unittest.TestCase):
    """Smoke tests of the CLI against the bundled sample data (no network)."""

    def cli(self, *args):
        with tempfile.TemporaryDirectory() as cache:
            return subprocess.run(
                [sys.executable, "-m", "umdsched", *args],
                cwd=ROOT, capture_output=True, text=True, env={"XDG_CACHE_HOME": cache, "XDG_CONFIG_HOME": cache, "PATH": ""},
            )

    def test_build_text_and_json(self):
        data = ["--data", "examples/sample-data.json", "--offline"]
        r = self.cli("CMSC216", "MATH240", "ENGL101|ENGL102", *data, "-n", "2")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("#1  cost", r.stdout)
        r = self.cli("build", "CMSC216", "CMSC250", *data, "--format", "json", "--days-off", "F")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertTrue(out["schedules"])
        self.assertTrue(all("F" not in s["campus_days"] for s in out["schedules"]))

    def test_config_file_and_ics(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, ics = Path(d) / "s.toml", Path(d) / "out.ics"
            r = self.cli("init-config", str(cfg))
            self.assertEqual(r.returncode, 0, r.stderr)
            r = self.cli("-c", str(cfg), "--data", "examples/sample-data.json", "--offline", "--ics", str(ics), "--term", "spring2027")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("BEGIN:VEVENT", ics.read_text())

    def test_bad_input_is_a_clean_error(self):
        r = self.cli("CS216", "--data", "examples/sample-data.json")
        self.assertEqual(r.returncode, 2)
        self.assertIn("doesn't look like a course id", r.stderr)
        r = self.cli("CMSC216", "--data", "examples/sample-data.json", "--earliest", "whenever")
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()


class FastPathAgreement(unittest.TestCase):
    """The search's incremental scorer must agree with the detailed one."""

    def test_costs_match_on_sample_data(self):
        import random

        from umdsched.sources import load_data_file

        courses = load_data_file(ROOT / "examples" / "sample-data.json")
        specs = ["CMSC216", "CMSC250", "MATH240", "ENGL101|ENGL102", "STAT400?"]
        for prefs in (Preferences(), Preferences(min_buffer=8, lunch_minutes=45, preferred_start=10 * 60)):
            sched = Scheduler(courses, [parse_spec(s) for s in specs], prefs)
            res = sched.search(keep=8)
            self.assertTrue(res.schedules)
            rng = random.Random(3)
            groups = []
            for gi, g in enumerate(sched.groups):
                opts = []
                for choice in g.alternatives:
                    from umdsched.scheduler import build_options

                    built, _ = build_options(courses[choice.course_id], choice, prefs)
                    for o in built:
                        o.group_index = gi
                        sched.prepare(o)
                    opts += built
                groups.append(opts)
            checked = 0
            for _ in range(300):
                partial = [rng.choice(opts) for opts in groups[:-1]]
                last = groups[-1]
                mask = (1 << len(last)) - 1
                fill = sched._fill_table([last])[0]
                bound, children = sched._expand_last(partial, mask, True, fill, last, sum(o.static_total for o in partial))
                for cost, opt in children:
                    full = partial + ([opt] if opt else [])
                    if any(sched._pair_ok(a, b) is False for i, x in enumerate(full) for y in full[i + 1:] for a in x.timed for b in y.timed):
                        continue
                    detail = sched._score(full, detail=True)
                    if detail is None:
                        self.assertIsNone(cost)
                        continue
                    self.assertAlmostEqual(cost, detail.cost, places=1)
                    self.assertAlmostEqual(sum(detail.breakdown.values()), detail.cost, places=1)
                    if bound is not None:
                        self.assertLessEqual(bound + sum(o.static_total for o in partial) + min(0, getattr(opt, "static_total", 0)), detail.cost + 1e-6)
                    checked += 1
            self.assertGreater(checked, 100)
