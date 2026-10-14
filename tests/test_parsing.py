import datetime as dt
import unittest

from umdsched.specs import parse_spec
from umdsched.models import Section
from umdsched.terms import approx_term_dates, default_term, parse_term
from umdsched.timeutil import format_time, parse_block, parse_days, parse_time, parse_time_range


class Times(unittest.TestCase):
    def test_parse_time(self):
        cases = {"10:00am": 600, "12:00pm": 720, "12:30am": 30, "2pm": 840, "14:30": 870, "9": 540, "3": 900, "9:30 AM": 570}
        for text, expected in cases.items():
            self.assertEqual(parse_time(text), expected, text)
        with self.assertRaises(ValueError):
            parse_time("noon-ish")

    def test_ranges_and_blocks(self):
        self.assertEqual(parse_time_range("11:00-14:00"), (660, 840))
        self.assertEqual(parse_time_range("11am-2pm"), (660, 840))
        self.assertEqual(parse_time_range("11-1"), (660, 780))
        self.assertEqual(parse_block("TuTh 3pm-6pm"), ((1, 3), 900, 1080))
        self.assertEqual(parse_block("F"), ((4,), 0, 1440))

    def test_days(self):
        self.assertEqual(parse_days("MWF"), (0, 2, 4))
        self.assertEqual(parse_days("TuTh"), (1, 3))
        self.assertEqual(parse_days("MTuWThF"), (0, 1, 2, 3, 4))
        self.assertEqual(parse_days("TR"), (1, 3))
        self.assertEqual(parse_days("mon, fri"), (0, 4))
        with self.assertRaises(ValueError):
            parse_days("MXF")

    def test_format(self):
        self.assertEqual(format_time(13 * 60 + 5), "1:05pm")
        self.assertEqual(format_time(0), "12:00am")


class Terms(unittest.TestCase):
    def test_default_term(self):
        self.assertEqual(default_term(dt.date(2026, 10, 14)), "202701")
        self.assertEqual(default_term(dt.date(2026, 4, 1)), "202608")
        self.assertEqual(default_term(dt.date(2027, 1, 20)), "202701")

    def test_parse_term(self):
        today = dt.date(2026, 10, 14)
        self.assertEqual(parse_term("202608"), "202608")
        self.assertEqual(parse_term("fall2026"), "202608")
        self.assertEqual(parse_term("Spring 27"), "202701")
        self.assertEqual(parse_term("spring", today), "202701")
        self.assertEqual(parse_term(None, today), "202701")
        with self.assertRaises(ValueError):
            parse_term("autumn")

    def test_term_dates_are_weekday_starts(self):
        start, end = approx_term_dates("202608")
        self.assertEqual(start.weekday(), 0)
        self.assertEqual(start.month, 8)
        self.assertLess(start, end)
        self.assertEqual(approx_term_dates("202701")[0].weekday(), 0)


class Specs(unittest.TestCase):
    def sec(self, sid, *profs):
        return Section("CMSC216", sid, list(profs))

    def test_plain(self):
        g = parse_spec("cmsc216")
        self.assertFalse(g.optional)
        self.assertEqual(g.alternatives[0].course_id, "CMSC216")
        self.assertTrue(g.alternatives[0].section_allowed(self.sec("0101")))

    def test_sections(self):
        c = parse_spec("CMSC216:0101,02*").alternatives[0]
        self.assertTrue(c.section_allowed(self.sec("0101")))
        self.assertTrue(c.section_allowed(self.sec("0205")))
        self.assertFalse(c.section_allowed(self.sec("0301")))
        c = parse_spec("CMSC216:~0201,!0301").alternatives[0]
        self.assertFalse(c.section_allowed(self.sec("0201")))
        self.assertFalse(c.section_allowed(self.sec("0301")))
        self.assertTrue(c.section_allowed(self.sec("0101")))
        self.assertEqual(parse_spec("CMSC216:101").alternatives[0].include_sections, ["0101"])

    def test_professors(self):
        c = parse_spec("CMSC216@herman").alternatives[0]
        self.assertTrue(c.section_allowed(self.sec("0101", "Larry Herman")))
        self.assertFalse(c.section_allowed(self.sec("0201", "Someone Else")))
        self.assertFalse(c.section_allowed(self.sec("0301")))  # TBA doesn't match
        c = parse_spec("CMSC216:01*@~Herman").alternatives[0]
        self.assertFalse(c.section_allowed(self.sec("0101", "Larry Herman")))
        self.assertTrue(c.section_allowed(self.sec("0102", "Someone Else")))
        self.assertFalse(c.section_allowed(self.sec("0201", "Someone Else")))

    def test_alternatives_and_optional(self):
        g = parse_spec("ENGL101|ENGL102/ENGL103?")
        self.assertTrue(g.optional)
        self.assertEqual([a.course_id for a in g.alternatives], ["ENGL101", "ENGL102", "ENGL103"])
        self.assertTrue(parse_spec("STAT400", optional=True).optional)
        self.assertEqual(parse_spec("MATH140H").alternatives[0].course_id, "MATH140H")
        with self.assertRaises(ValueError):
            parse_spec("CS216")


if __name__ == "__main__":
    unittest.main()
